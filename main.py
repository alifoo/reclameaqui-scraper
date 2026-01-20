import os
import time
import re
import csv
import random
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError, expect
from playwright_stealth import Stealth
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps

SEGMENTS_PAGE_URL = "https://www.reclameaqui.com.br/segmentos/"
OUTPUT_FILENAME = "reclameaqui_complaints.csv"
MAX_RETRIES = 5
NAVIGATION_TIMEOUT = 90000
SELECTOR_TIMEOUT = 40000
MIN_DELAY_BETWEEN_COMPLAINTS = 5
MAX_DELAY_BETWEEN_COMPLAINTS = 10
MIN_DELAY_BETWEEN_PAGES = 15
MAX_DELAY_BETWEEN_PAGES = 25
CAPTCHA_COOLDOWN = 300

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


def retry_on_failure(
    max_attempts=MAX_RETRIES, backoff_factor=2, exceptions=(TimeoutError, Exception)
):
    """Decorator to retry a function on failure with exponential backoff"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Try to extract company_name from args or kwargs for better logging
            company_name = kwargs.get("company_name") or (
                args[0] if args and isinstance(args[0], str) else "Unknown"
            )

            attempt = 0
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        print(
                            f"[{company_name}][RETRY] Max attempts ({max_attempts}) reached for {func.__name__}. Last error: {e}"
                        )
                        raise
                    wait_time = backoff_factor**attempt + random.uniform(0, 1)
                    print(
                        f"[{company_name}][RETRY] Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}"
                    )
                    print(
                        f"[{company_name}][RETRY] Waiting {wait_time:.2f}s before retry..."
                    )
                    time.sleep(wait_time)
            return None

        return wrapper

    return decorator


def get_random_ua():
    return random.choice(USER_AGENTS)


def check_for_captcha(page, company_name="Unknown"):
    """Check if CAPTCHA page is displayed"""
    url = page.url
    content = page.content().lower()

    captcha_indicators = [
        "verify-human" in url,
        "verify you are human" in content,
        "cloudflare" in content and "ray id" in content,
        "checking your browser" in content,
    ]

    if any(captcha_indicators):
        print(f"[{company_name}][CAPTCHA] CAPTCHA DETECTED!")
        return True
    return False


def handle_captcha(page, company_name="Unknown", auto_wait=True):
    """Handle CAPTCHA detection"""
    print(f"[{company_name}][CAPTCHA] CAPTCHA page detected!")
    print(f"[{company_name}][CAPTCHA] URL: {page.url}")

    if auto_wait:
        print(
            f"[{company_name}][CAPTCHA] Cooling down for {CAPTCHA_COOLDOWN} seconds ({CAPTCHA_COOLDOWN//60} minutes)..."
        )
        print(
            f"[{company_name}][CAPTCHA] This allows the site to 'forget' about our scraping activity"
        )
        time.sleep(CAPTCHA_COOLDOWN)
        return True
    else:
        print(f"[{company_name}][CAPTCHA] ==========================================")
        print(f"[{company_name}][CAPTCHA] MANUAL INTERVENTION REQUIRED")
        print(f"[{company_name}][CAPTCHA] ==========================================")
        print(
            f"[{company_name}][CAPTCHA] The browser window should be visible (headless=False)"
        )
        print(
            f"[{company_name}][CAPTCHA] Please solve the CAPTCHA manually in the browser"
        )
        print(f"[{company_name}][CAPTCHA] The script will wait for 120 seconds...")
        print(f"[{company_name}][CAPTCHA] ==========================================")

        time.sleep(120)

        if check_for_captcha(page, company_name):
            print(
                f"[{company_name}][CAPTCHA] Still on CAPTCHA page. Waiting another 60 seconds..."
            )
            time.sleep(60)

        return not check_for_captcha(page, company_name)


def save_incremental(data_list, filename):
    if not data_list:
        return
    file_exists = os.path.isfile(filename)
    keys = data_list[0].keys()

    try:
        with open(filename, "a", newline="", encoding="utf-8-sig") as output_file:
            dict_writer = csv.DictWriter(
                output_file, fieldnames=keys, delimiter=";", quoting=csv.QUOTE_ALL
            )
            if not file_exists:
                dict_writer.writeheader()
            dict_writer.writerows(data_list)
        print(f"[SAVED] {len(data_list)} records saved to disk.")
    except Exception as e:
        print(f"[ERROR] Could not save data: {e}")


def get_starting_point(filename, companies):
    default_return = (companies[0], 1, 0, companies)
    if not os.path.isfile(filename):
        return default_return

    try:
        df = pd.read_csv(filename, sep=";")
        if df.empty:
            print("df empty")
            return default_return

        last_row = df.iloc[-1]
        last_company = last_row.get("company_name")

        def safe_int(val, default):
            try:
                return int(float(val))
            except:
                return default

        last_page = safe_int(last_row.get("page"), 1)
        last_total = safe_int(last_row.get("total_pages"), 1)
        last_complaint = safe_int(last_row.get("complaint_number"), 0)

        if last_page < last_total:
            print(
                f"Resuming {last_company} at Page {last_page}, Complaint {last_complaint}"
            )
            return (last_company, last_page, last_complaint + 1, companies)
        else:
            if last_company in companies:
                current_idx = companies.index(last_company)
                if current_idx + 1 < len(companies):
                    next_company = companies[current_idx + 1]
                    remaining_companies = companies[current_idx + 1 :]
                    print(
                        f"Previous company {last_company} done. Moving to {next_company}."
                    )
                    return (next_company, 1, 0, remaining_companies)
                else:
                    return (None, None, None, [])
            else:
                remaining = [c for c in companies if c != last_company]
                if remaining:
                    return (remaining[0], 1, 0, remaining)
                return (None, None, None, [])
    except Exception as e:
        print(
            f"Warning: Could not read existing file to resume. Starting fresh. Error: {e}"
        )
        return companies[0], 1, 0, companies


def check_cookie(page, company_name="Unknown"):
    try:
        print(f"[{company_name}][COOKIE] Checking for cookie banner...")
        accept_button = page.locator("#adopt-accept-all-button")
        accept_button.click(timeout=10000)
        print(f"[{company_name}][COOKIE] Banner accepted.")
    except TimeoutError:
        print(f"[{company_name}][COOKIE] No banner found or already dismissed.")
    """Check if CAPTCHA page is displayed"""
    url = page.url
    content = page.content().lower()

    captcha_indicators = [
        "verify-human" in url,
        "verify you are human" in content,
        "cloudflare" in content and "ray id" in content,
        "checking your browser" in content,
    ]

    if any(captcha_indicators):
        print(f"[{company_name}][CAPTCHA] CAPTCHA DETECTED!")
        return True
    return False


@retry_on_failure(max_attempts=3, exceptions=(TimeoutError,))
def safe_navigate(
    page,
    url,
    wait_until="domcontentloaded",
    timeout=NAVIGATION_TIMEOUT,
    company_name="Unknown",
):
    """Navigate with retry logic"""
    print(f"[{company_name}][NAV] Navigating to {url[:100]}...")
    page.goto(url, wait_until=wait_until, timeout=timeout)
    print(f"[{company_name}][NAV] Successfully loaded page")
    return True


@retry_on_failure(max_attempts=3, exceptions=(TimeoutError,))
def safe_wait_for_selector(
    page, selector, timeout=SELECTOR_TIMEOUT, company_name="Unknown"
):
    """Wait for selector with retry logic"""
    print(f"[{company_name}][WAIT] Waiting for selector: {selector}")
    page.wait_for_selector(selector, timeout=timeout)
    print(f"[{company_name}][WAIT] Selector found: {selector}")
    return True


@retry_on_failure(max_attempts=2, exceptions=(Exception,))
def safe_click(element, delay=None, company_name="Unknown"):
    """Click with retry logic"""
    if delay:
        element.click(delay=delay)
    else:
        element.click()
    return True


def fetch_complaint_info(soup, i, page_number, total_pages, company_name="Unknown"):
    complaint_data = {}
    print(f"[{company_name}][EXTRACT] Processing complaint {i}")

    def safe_get_text(element):
        return element.get_text(strip=True) if element else "Not found"

    complaint_container = soup.select_one(".sc-98c0be-3.fmbfWT")

    if complaint_container:
        print(f"[{company_name}][EXTRACT] Container found, extracting details...")

        complaint_data["location"] = safe_get_text(
            complaint_container.select_one("[data-testid='complaint-location']")
        )
        complaint_data["date"] = safe_get_text(
            complaint_container.select_one("[data-testid='complaint-creation-date']")
        )
        complaint_data["full_title"] = safe_get_text(
            soup.select_one("[data-testid='complaint-title']")
        )
        complaint_data["full_description"] = safe_get_text(
            soup.select_one("p[data-testid='complaint-description']")
        )

        response_header = soup.find("div", type="ANSWER")
        complaint_data["company_response"] = (
            safe_get_text(response_header.find_next_sibling("p"))
            if response_header
            else "Not found"
        )

        consideration_header = soup.find("div", type="FINAL_ANSWER")
        complaint_data["final_consideration"] = (
            safe_get_text(consideration_header.find_next_sibling("p"))
            if consideration_header
            else "Not found"
        )

        evaluation_panel = soup.select_one(
            "div[data-testid='complaint-evaluation-interaction']"
        )
        if evaluation_panel:
            complaint_data["solved"] = safe_get_text(
                evaluation_panel.select_one("div[data-testid='complaint-status']")
            )
            deal_again_header = evaluation_panel.find(
                "span", string="Voltaria a fazer negócio?"
            )
            complaint_data["deal_again"] = (
                safe_get_text(deal_again_header.find_next_sibling("div").find("div"))
                if deal_again_header
                else "Not found"
            )

            score_header = evaluation_panel.find("span", string="Nota do atendimento")
            complaint_data["score"] = (
                safe_get_text(score_header.find_next_sibling("div").find("div"))
                if score_header
                else "Not found"
            )
            complaint_data["complaint_number"] = str(i)
            complaint_data["page"] = str(page_number)
            complaint_data["total_pages"] = str(total_pages)

            print(f"[{company_name}][EXTRACT] Details extracted successfully")
        else:
            print(f"[{company_name}][EXTRACT] Evaluation panel not found")
            complaint_data.update(
                {
                    "solved": "Not found",
                    "deal_again": "Not found",
                    "score": "Not found",
                    "complaint_number": str(i),
                    "page": str(page_number),
                    "total_pages": str(total_pages),
                }
            )
    else:
        print(f"[{company_name}][EXTRACT] Container not found for complaint {i}")
        complaint_data = {
            "location": "Not found",
            "date": "Not found",
            "full_title": "Not found",
            "full_description": "Not found",
            "company_response": "Not found",
            "final_consideration": "Not found",
            "solved": "Not found",
            "deal_again": "Not found",
            "score": "Not found",
            "complaint_number": str(i),
            "page": str(page_number),
            "total_pages": str(total_pages),
        }

    return complaint_data


def scrape_complaints(company_name: str, start_page: int, start_complaint: int):
    print(f"\n{'='*60}")
    print(f"[{company_name}][START] Scraping {company_name}")
    print(f"{'='*60}")

    base_url = "https://www.reclameaqui.com.br"
    url = f"{base_url}/empresa/{company_name}/lista-reclamacoes/?status=EVALUATED"

    def block_heavy_resources(route):
        if route.request.resource_type in [
            "image",
            "media",
            "font",
            "stylesheet",
            "other",
        ]:
            route.abort()
        else:
            route.continue_()

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-gl-drawing-for-tests",
            ],
        )
        context = browser.new_context(
            user_agent=get_random_ua(),
            viewport={"width": 1920, "height": 1080},
        )
        context.route("**/*", block_heavy_resources)
        main_page = context.new_page()

        try:
            safe_navigate(main_page, url, company_name=company_name)

            if check_for_captcha(main_page, company_name):
                handle_captcha(main_page, company_name, auto_wait=True)
                safe_navigate(main_page, url, company_name=company_name)

            check_cookie(main_page, company_name)

            total_pages_selector = ".sc-1sm4sxr-0.iwOeoe"
            safe_wait_for_selector(
                main_page, total_pages_selector, company_name=company_name
            )

            pages_to_scrape = main_page.get_by_test_id("pages-label")
            pages_to_scrape.wait_for(state="visible", timeout=10000)

            text = pages_to_scrape.inner_text()
            match = re.search(r"de\s+([\d\.]+)", text)
            if match:
                total_pages = int(match.group(1).replace(".", ""))
            else:
                print(
                    f"[{company_name}][WARN] Could not parse page number. Defaulting to 1."
                )
                total_pages = 1

            print(f"[{company_name}][INFO] Total pages to scrape: {total_pages}")

            for page_num in range(start_page, total_pages + 1):
                page_data = []
                print(f"\n[{company_name}][PAGE] Scraping {page_num}/{total_pages}")

                if check_for_captcha(main_page, company_name):
                    handle_captcha(main_page, company_name, auto_wait=True)
                    safe_navigate(main_page, url, company_name=company_name)
                    if page_num > 1:
                        for _ in range(page_num - 1):
                            next_btn = main_page.get_by_test_id(
                                "next-page-navigation-button"
                            )
                            if next_btn.is_visible():
                                safe_click(next_btn, company_name=company_name)
                                time.sleep(random.uniform(2, 4))
                    continue

                try:
                    safe_wait_for_selector(
                        main_page, total_pages_selector, company_name=company_name
                    )
                except Exception as e:
                    print(
                        f"[{company_name}][ERROR] Failed to load page {page_num} after retries: {e}"
                    )
                    continue

                first_title_element = main_page.locator("h4.sc-1pe7b5t-1.bVKmkO").first
                old_title_text = first_title_element.inner_text()

                html_content = main_page.content()
                soup = BeautifulSoup(html_content, "html.parser")
                complaint_containers = soup.select("div.sc-1pe7b5t-0.eJgBOc")

                if not complaint_containers:
                    print(
                        f"[{company_name}][WARN] No complaints found on page {page_num}. Skipping."
                    )
                    continue

                page_links = []
                for complaint in complaint_containers:
                    link_tag = complaint.find("a", id="site_bp_lista_ler_reclamacao")
                    if link_tag:
                        page_links.append(
                            {
                                "company_name": company_name,
                                "url": base_url + link_tag["href"],
                            }
                        )

                complaints_to_visit = page_links[start_complaint:]
                list_page_url = main_page.url

                for i, link_info in enumerate(
                    complaints_to_visit, start=start_complaint
                ):
                    print(
                        f"\n[{company_name}][COMPLAINT {i}/{len(page_links)}] {link_info['url']}"
                    )

                    if i > start_complaint:
                        delay = random.uniform(
                            MIN_DELAY_BETWEEN_COMPLAINTS, MAX_DELAY_BETWEEN_COMPLAINTS
                        )
                        print(
                            f"[{company_name}][DELAY] Waiting {delay:.1f}s before next complaint..."
                        )
                        time.sleep(delay)

                    retry_count = 0
                    max_complaint_retries = 3

                    while retry_count < max_complaint_retries:
                        try:
                            safe_navigate(
                                main_page, link_info["url"], company_name=company_name
                            )

                            if check_for_captcha(main_page, company_name):
                                handle_captcha(main_page, company_name, auto_wait=True)
                                safe_navigate(
                                    main_page,
                                    link_info["url"],
                                    company_name=company_name,
                                )

                            try:
                                safe_wait_for_selector(
                                    main_page,
                                    ".sc-98c0be-3.fmbfWT",
                                    timeout=60000,
                                    company_name=company_name,
                                )
                                time.sleep(random.uniform(1.5, 3.0))
                            except Exception as wait_error:
                                print(
                                    f"[{company_name}][RETRY] Container not loaded, attempt {retry_count + 1}/{max_complaint_retries}"
                                )
                                retry_count += 1
                                if retry_count >= max_complaint_retries:
                                    print(
                                        f"[{company_name}][SKIP] Skipping complaint {i} after {max_complaint_retries} attempts"
                                    )
                                    break
                                time.sleep(2)
                                continue

                            detail_soup = BeautifulSoup(
                                main_page.content(), "html.parser"
                            )
                            details = fetch_complaint_info(
                                detail_soup,
                                i,
                                page_num,
                                total_pages,
                                company_name=company_name,
                            )
                            link_info.update(details)
                            page_data.append(link_info)

                            break

                        except Exception as e:
                            retry_count += 1
                            print(
                                f"[{company_name}][ERROR] Complaint {i} error (attempt {retry_count}/{max_complaint_retries}): {e}"
                            )
                            if retry_count >= max_complaint_retries:
                                print(
                                    f"[{company_name}][SKIP] Skipping complaint {i} after errors"
                                )
                                break
                            time.sleep(random.uniform(2, 4))

                    try:
                        safe_navigate(
                            main_page,
                            list_page_url,
                            timeout=60000,
                            company_name=company_name,
                        )
                        safe_wait_for_selector(
                            main_page,
                            total_pages_selector,
                            timeout=30000,
                            company_name=company_name,
                        )
                    except Exception as e:
                        print(
                            f"[{company_name}][ERROR] Failed to return to list page: {e}"
                        )
                        try:
                            safe_navigate(main_page, url, company_name=company_name)
                            if page_num > 1:
                                for _ in range(page_num - 1):
                                    next_btn = main_page.get_by_test_id(
                                        "next-page-navigation-button"
                                    )
                                    if next_btn.is_visible():
                                        safe_click(next_btn, company_name=company_name)
                                        time.sleep(2)
                        except:
                            print(
                                f"[{company_name}][ERROR] Could not recover navigation. Moving to next company."
                            )
                            break

                save_incremental(page_data, OUTPUT_FILENAME)
                start_complaint = 0

                print(f"[{company_name}][PAGE] Completed page {page_num}")

                if page_num >= total_pages:
                    print(f"[{company_name}][INFO] Reached final page")
                    break

                delay = random.uniform(MIN_DELAY_BETWEEN_PAGES, MAX_DELAY_BETWEEN_PAGES)
                print(
                    f"[{company_name}][DELAY] Waiting {delay:.1f}s before next page..."
                )
                time.sleep(delay)

                next_page_button = main_page.get_by_test_id(
                    "next-page-navigation-button"
                )

                if next_page_button.is_visible():
                    print(f"[{company_name}][NAV] Moving to next page...")

                    page_changed = False
                    for nav_attempt in range(3):
                        try:
                            current_page_text = main_page.get_by_test_id(
                                "pages-label"
                            ).inner_text()

                            next_page_button.hover()
                            time.sleep(random.uniform(0.5, 1.0))
                            safe_click(
                                next_page_button,
                                delay=random.randint(50, 150),
                                company_name=company_name,
                            )

                            time.sleep(random.uniform(2, 3))

                            try:
                                new_page_text = main_page.get_by_test_id(
                                    "pages-label"
                                ).inner_text()
                                if new_page_text != current_page_text:
                                    print(
                                        f"[{company_name}][NAV] Page updated: {current_page_text} -> {new_page_text}"
                                    )
                                    page_changed = True
                                    break
                                else:
                                    print(
                                        f"[{company_name}][NAV] Page number unchanged (attempt {nav_attempt + 1}/3), retrying..."
                                    )
                                    time.sleep(1)
                            except:
                                try:
                                    new_title_text = first_title_element.inner_text()
                                    if new_title_text != old_title_text:
                                        print(
                                            f"[{company_name}][NAV] Page content changed (verified by title)"
                                        )
                                        page_changed = True
                                        break
                                except:
                                    pass

                        except Exception as e:
                            print(
                                f"[{company_name}][WARN] Navigation attempt {nav_attempt + 1}/3 failed: {e}"
                            )
                            time.sleep(random.uniform(1, 2))

                    if page_changed:
                        print(
                            f"[{company_name}][NAV] Successfully moved to page {page_num + 1}"
                        )
                    else:
                        print(
                            f"[{company_name}][ERROR] Failed to navigate to next page after 3 attempts"
                        )
                        try:
                            next_page_url = f"{url}&pagina={page_num + 1}"
                            print(
                                f"[{company_name}][NAV] Attempting manual navigation to: {next_page_url}"
                            )
                            safe_navigate(
                                main_page, next_page_url, company_name=company_name
                            )
                            safe_wait_for_selector(
                                main_page,
                                total_pages_selector,
                                company_name=company_name,
                            )
                            time.sleep(random.uniform(3, 5))
                        except Exception as manual_nav_error:
                            print(
                                f"[{company_name}][ERROR] Manual navigation failed: {manual_nav_error}"
                            )
                            print(
                                f"[{company_name}][ERROR] Stopping pagination for this company"
                            )
                            break
                else:
                    print(f"[{company_name}][INFO] No next page button found")
                    break

        except Exception as e:
            print(f"[{company_name}][CRITICAL] Error scraping {company_name}: {e}")
        finally:
            print(f"\n[{company_name}][DONE] Finished scraping")
            context.close()
            browser.close()


def scrape_company_names(main_page, category):
    try:
        ranking_selector = "ranking"
        main_page.get_by_test_id(ranking_selector).wait_for(timeout=5000)
        print(f"[INFO] Company names loaded for {category}")
    except TimeoutError:
        print("[ERROR] Timeout waiting for company names")
        return []
    except Exception as e:
        print(f"[ERROR] {e}")
        return []

    company_locators = main_page.locator(
        "div.rs-flex.rs-items-center a[href*='/empresa/']"
    )

    all_hrefs = []
    for i in range(company_locators.count()):
        href = company_locators.nth(i).get_attribute("href")
        if href:
            all_hrefs.append(href)

    company_names = []
    for href in all_hrefs:
        match = re.search(r"/empresa/([^/]+)", href)
        if match:
            company_names.append(match.group(1))

    print(f"[INFO] Found {len(company_names)} companies in {category}")
    return company_names


def get_best_ranked_companies_by_category(category, category_url):
    ranked_companies_names = []
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=get_random_ua(),
            viewport={"width": 1920, "height": 1080},
        )
        main_page = context.new_page()

        try:
            safe_navigate(main_page, category_url)
            check_cookie(main_page)
            company_names = scrape_company_names(main_page, category)
            if company_names:
                ranked_companies_names.extend(company_names)
        except Exception as e:
            print(f"[ERROR] Failed to get companies for {category}: {e}")
        finally:
            browser.close()

    return ranked_companies_names


def execute(category_name, category_url):
    print(f"\n{'#'*60}")
    print(f"STARTING SCRAPER FOR: {category_name}")
    print(f"{'#'*60}\n")

    companies = get_best_ranked_companies_by_category(category_name, category_url)

    if not companies:
        print("[ERROR] No companies found. Exiting.")
        return

    resume_company, resume_page, resume_complaint, companies_to_process = (
        get_starting_point(OUTPUT_FILENAME, companies)
    )

    if not companies_to_process:
        print("[INFO] All companies finished! Exiting.")
        return

    print(
        f"[RESUME] Company: {resume_company}, Page: {resume_page}, Complaint: {resume_complaint}"
    )
    print(f"[INFO] Companies remaining: {len(companies_to_process)}")

    print(f"[INFO] Processing companies sequentially to avoid CAPTCHA...")

    for comp in companies_to_process:
        if comp == resume_company:
            current_start_page = resume_page
            current_start_complaint = resume_complaint
        else:
            current_start_page = 1
            current_start_complaint = 0

        try:
            print(f"\n[MAIN] Starting scrape for company: {comp}")
            scrape_complaints(comp, current_start_page, current_start_complaint)
            print(f"[SUCCESS] Completed {comp}")
        except Exception as e:
            print(f"[FAILED] Job failed for {comp}: {e}")
            continue


if __name__ == "__main__":
    cat_name = "Brinquedos e Entretenimento Infantil"
    cat_url = "https://www.reclameaqui.com.br/segmentos/arte-e-entretenimento/brinquedos-e-entretenimento-infantil/"

    execute(cat_name, cat_url)
