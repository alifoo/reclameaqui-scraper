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

SEGMENTS_PAGE_URL = "https://www.reclameaqui.com.br/segmentos/"
OUTPUT_FILENAME = "reclameaqui_complaints.csv"
MAX_RETRIES = 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


def get_random_ua():
    return random.choice(USER_AGENTS)


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


def check_cookie(page):
    try:
        print("Checking for cookie banner...")
        accept_button = page.locator("#adopt-accept-all-button")
        accept_button.click(timeout=10000)
        print("Cookie banner accepted.")
    except TimeoutError:
        print("No cookie banner found or it was already dismissed.")


def fetch_complaint_info(soup, i, page_number, total_pages):
    complaint_data = {}
    print(f"Opened complaint detail page for complaint {i}")

    def safe_get_text(element):
        return element.get_text(strip=True) if element else "Not found"

    complaint_container = soup.select_one(".sc-98c0be-3.fmbfWT")

    if complaint_container:
        print("Complaint container found, extracting details...")

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

            print("Details extracted successfully.")
        else:
            print("Details not extracted (not found).")
            complaint_data.update(
                {"solved": "Not found", "deal_again": "Not found", "score": "Not found"}
            )
    else:
        print(f"Complaint container not found for complaint {i}")
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
            "complaint_number": "None",
            "page": "None",
            "total_pages": "None",
        }

    return complaint_data


def scrape_complaints(company_name: str, start_page: int, start_complaint: int):
    print(f"Starting to scrape {company_name}...")

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
            print(f"Navigating to {url}...")
            main_page.goto(url, wait_until="domcontentloaded", timeout=90000)
            check_cookie(main_page)

            total_pages_selector = ".sc-1sm4sxr-0.iwOeoe"
            try:
                main_page.wait_for_selector(total_pages_selector, timeout=20000)
            except TimeoutError:
                print(
                    f"Timeout while waiting for first complaint list (to get total pages number)"
                )
                return

            pages_to_scrape = main_page.get_by_test_id("pages-label")
            pages_to_scrape.wait_for(state="visible", timeout=10000)

            text = pages_to_scrape.inner_text()
            match = re.search(r"de\s+([\d\.]+)", text)
            if match:
                total_pages = int(match.group(1).replace(".", ""))
            else:
                print("Could not parse page number from text. Defaulting to 1.")
                total_pages = 1

            for page_num in range(start_page, total_pages + 1):
                page_data = []
                print(
                    f"\n--- Scraping Page {page_num}/{total_pages} for {company_name} ---"
                )

                if "verify-human" in main_page.url:
                    print("!!! CAPTCHA/BLOCK DETECTED. COOLING DOWN FOR 5 MINUTES !!!")
                    time.sleep(300)
                    continue

                print(
                    f"Waiting for complaint list to load using selector: {total_pages_selector}"
                )

                try:
                    print(f"Waiting for complaint list: {total_pages_selector}")
                    main_page.wait_for_selector(total_pages_selector, timeout=20000)
                    print("Complaint list loaded.")
                except TimeoutError:
                    print(
                        f"Timeout while waiting for complaint list on page {page_num}, skipping page."
                    )
                    continue

                first_title_element = main_page.locator("h4.sc-1pe7b5t-1.bVKmkO").first
                old_title_text = first_title_element.inner_text()

                html_content = main_page.content()
                soup = BeautifulSoup(html_content, "html.parser")
                complaint_containers = soup.select("div.sc-1pe7b5t-0.eJgBOc")
                if not complaint_containers:
                    print(
                        f"No complaint containers found on page {page_num}. Skipping."
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
                    print(f"-> Complaint {i}: {link_info['url']}")
                    try:
                        main_page.goto(
                            link_info["url"],
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )

                        try:
                            main_page.wait_for_selector(
                                ".sc-98c0be-3.fmbfWT", timeout=45000
                            )
                            time.sleep(random.uniform(1.5, 3.0))
                        except TimeoutError:
                            print(
                                f"Complaint container didn't load for complaint {i}, retrying..."
                            )

                            main_page.reload(wait_until="domcontentloaded")
                            try:
                                main_page.wait_for_selector(
                                    ".sc-98c0be-3.fmbfWT", timeout=10000
                                )
                                time.sleep(random.uniform(2.0, 3.5))
                            except TimeoutError:
                                print(
                                    f"Complaint container still not found after retry, skipping..."
                                )
                                main_page.go_back(wait_until="domcontentloaded")
                                continue

                        detail_soup = BeautifulSoup(main_page.content(), "html.parser")

                        details = fetch_complaint_info(
                            detail_soup, i, page_num, total_pages
                        )

                        link_info.update(details)
                        page_data.append(link_info)

                        main_page.goto(
                            list_page_url, wait_until="domcontentloaded", timeout=60000
                        )

                        try:
                            main_page.wait_for_selector(
                                total_pages_selector, timeout=30000
                            )
                        except TimeoutError:
                            print(f"Lost the list page, retrying navigation...")
                            time.sleep(random.uniform(2, 4))
                            main_page.goto(
                                list_page_url,
                                wait_until="domcontentloaded",
                                timeout=60000,
                            )
                            main_page.wait_for_selector(
                                total_pages_selector, timeout=45000
                            )
                    except Exception as e:
                        print(f"Error extracting complaint: {e}")
                        try:
                            main_page.goto(url, wait_until="domcontentloaded")
                        except:
                            pass

                save_incremental(page_data, OUTPUT_FILENAME)
                start_complaint = 0

                print(f"Finished scraping details for page {page_num}.")

                if page_num >= total_pages:
                    print("reached limit. stopping.")
                    break

                next_page_button = main_page.get_by_test_id(
                    "next-page-navigation-button"
                )

                if next_page_button.is_visible():
                    print("Moving to next page...")
                    next_page_button.hover()
                    time.sleep(random.uniform(0.5, 1.5))
                    next_page_button.click(delay=random.randint(50, 150))

                    print("Waiting for new content...")
                    try:
                        expect(first_title_element).not_to_have_text(
                            old_title_text, timeout=30000
                        )
                        print("Page updated.")
                        time.sleep(random.uniform(4, 6))
                    except:
                        print("Page did not update visually, but continuing...")
                else:
                    print("could not find 'Next Page' button.")
                    break

        except Exception as e:
            print(f"Critical error scraping company {company_name}: {e}")
        finally:
            print("\nScraping finished. Closing browser.")
            context.close()
            browser.close()


def expand_buttons(main_page, segments):
    expand_buttons_locator = main_page.locator("button[aria-controls$='-acordeon']")
    button_count = expand_buttons_locator.count()
    print(f"Found {button_count} buttons to click.")

    if len(segments) != button_count:
        print("Segments and button_count are different. Check the index.")

    for i in range(button_count - 29):
        print(f"Clicking button {i + 1}.")
        button = expand_buttons_locator.nth(i)
        if button.is_visible():
            button.hover()
            time.sleep(random.uniform(0.5, 1.5))
            button.click(delay=random.randint(50, 150))

    print("\nAll accordions expanded. Now finding all segment links...")


def scrape_company_names(main_page, category):
    try:
        ranking_selector = "ranking"
        main_page.get_by_test_id(ranking_selector).wait_for(timeout=5000)
        print(f"Best company names loaded for {category} category.")
    except TimeoutError:
        print("Timeout while waiting for company names page load.")
        return []
    except Exception as e:
        print(e)
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

    print(f"Found {len(company_names)} companies inside {category} page.")
    return company_names


def get_best_ranked_companies():
    ranked_companies_names = []
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        main_page = context.new_page()

        print(f"Navigating to {SEGMENTS_PAGE_URL}...")
        main_page.goto(SEGMENTS_PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        check_cookie(main_page)

        try:
            accordion_locators = main_page.locator("div.rs-acordeon")
            accordion_count = accordion_locators.count()
            print(f"Found {accordion_count} segment accordions to process.")

            for i in range(accordion_count):
                accordion = main_page.locator("div.rs-acordeon").nth(i)
                header_button = accordion.locator("button[aria-controls]")
                header_text = header_button.text_content()
                print(
                    f"\n--- Processing Accordion {i + 1}/{accordion_count}: {header_text.strip()} ---"
                )

                header_button.scroll_into_view_if_needed()
                if header_button.get_attribute("aria-expanded") == "false":
                    header_button.click()
                    time.sleep(0.5)

                links_in_accordion = accordion.locator("a[href*='/segmentos/']")
                links_count_inside = links_in_accordion.count()
                print(f"Found {links_count_inside} links in this section.")

                for j in range(links_count_inside):
                    try:
                        current_accordion = main_page.locator("div.rs-acordeon").nth(i)
                        current_expand_button = current_accordion.locator(
                            "button[aria-controls]"
                        )

                        current_expand_button.scroll_into_view_if_needed()
                        if (
                            current_expand_button.get_attribute("aria-expanded")
                            == "false"
                        ):
                            print(f"Re-opening accordion for link {j + 1}...")
                            current_expand_button.click()
                            time.sleep(0.5)

                        link_to_click = current_accordion.locator(
                            "a[href*='/segmentos/']"
                        ).nth(j)
                        link_text = link_to_click.text_content()
                        print(
                            f"Processing link {j + 1}/{links_count_inside}: {link_text.strip()}"
                        )

                        link_to_click.click()

                        company_names = scrape_company_names(
                            main_page, link_text.strip()
                        )
                        if company_names:
                            ranked_companies_names.extend(company_names)

                        print("Navigating back to the segments list...")
                        main_page.go_back(wait_until="domcontentloaded")
                        main_page.locator("div.rs-acordeon").first.wait_for()

                    except Exception as e:
                        print(f"An error occurred on inner link #{j+1}: {e}")
                        main_page.goto(SEGMENTS_PAGE_URL, wait_until="domcontentloaded")

        except Exception as e:
            print(f"A major error occurred in the outer loop: {e}")
        finally:
            browser.close()

    # quick fix. TODO
    unique_companies = list(dict.fromkeys(ranked_companies_names))

    print("\n--- Scraping company names complete ---")
    print(f"Collected a total of {len(unique_companies)} company names.")

    df = pd.DataFrame(unique_companies, columns=["company_name"])
    df.to_csv("best_ranked_companies.csv", index=False, encoding="utf-8-sig")
    print("Data saved to best_ranked_companies.csv")

    return ranked_companies_names


def get_best_ranked_companies_by_category(category, category_url):
    ranked_companies_names = []
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        main_page = context.new_page()

        print(f"Navigating to {category_url}...")
        main_page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
        check_cookie(main_page)

        company_names = scrape_company_names(main_page, category)
        if company_names:
            ranked_companies_names.extend(company_names)

    return ranked_companies_names


def execute(category_name, category_url):
    companies = get_best_ranked_companies_by_category(category_name, category_url)

    resume_company, resume_page, resume_complaint, companies_to_process = (
        get_starting_point(OUTPUT_FILENAME, companies)
    )

    if not companies_to_process:
        print("All companies finished! Exiting.")
        return

    print(
        f"Resuming from: {resume_company} at Page {resume_page}, Complaint {resume_complaint}"
    )
    print(f"Companies left to process: {len(companies_to_process)}")

    max_threads = 1
    with ThreadPoolExecutor(max_threads) as executor:
        futures = {}
        for comp in companies_to_process:
            if comp == resume_company:
                current_start_page = resume_page
                current_start_complaint = resume_complaint
            else:
                current_start_page = 1
                current_start_complaint = 0

            future = executor.submit(
                scrape_complaints, comp, current_start_page, current_start_complaint
            )
            futures[future] = comp
        for future in as_completed(futures):
            company = futures[future]
            try:
                future.result()
                print(f"Done with {company}")
            except Exception as e:
                print(f"Job failed for {company}: {e}")


if __name__ == "__main__":
    cat_name = "Brinquedos e Entretenimento Infantil"
    cat_url = "https://www.reclameaqui.com.br/segmentos/arte-e-entretenimento/brinquedos-e-entretenimento-infantil/"

    execute(cat_name, cat_url)
