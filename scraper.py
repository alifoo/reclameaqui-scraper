import time
import csv
import random
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError, expect
from playwright_stealth import Stealth
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

SEGMENTS_PAGE_URL = "https://www.reclameaqui.com.br/segmentos/"


def check_cookie(page):
    try:
        print("Checking for cookie banner...")
        accept_button = page.locator("#adopt-accept-all-button")
        accept_button.click(timeout=20000)
        print("Cookie banner accepted.")
    except TimeoutError:
        print("No cookie banner found or it was already dismissed.")


def fetch_complaint_info(soup, i):
    complaint_data = {}
    print(f"Opened complaint detail page for complaint {i + 1}")

    def safe_get_text(element):
        return element.get_text(strip=True) if element else "Not found"

    complaint_container = soup.select_one(".sc-98c0be-3.fmbfWT")

    if complaint_container:
        print("Complaint container found, extracting details...")

        complaint_data["location"] = safe_get_text(
            complaint_container.select_one("[data-testid='complaint-location']")
        )
        complaint_data["detailed_date"] = safe_get_text(
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
        print("Details extracted successfully.")
    else:
        complaint_data = {
            "location": "Not found",
            "detailed_date": "Not found",
            "full_title": "Not found",
            "full_description": "Not found",
            "company_response": "Not found",
            "final_consideration": "Not found",
            "solved": "Not found",
            "deal_again": "Not found",
            "score": "Not found",
        }

    return complaint_data


def scrape_complaints(company_name: str, pages_to_scrape: int):
    print(f"Starting to scrape {company_name}...")
    base_url = "https://www.reclameaqui.com.br"
    start_url = f"{base_url}/empresa/{company_name}/lista-reclamacoes/?status=EVALUATED"
    url = f"https://www.reclameaqui.com.br/empresa/{company_name}/lista-reclamacoes/?status=EVALUATED"
    all_complaints_data = []

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        main_page = context.new_page()

        print(f"Navigating to {url}...")
        main_page.goto(url, wait_until="domcontentloaded", timeout=60000)

        check_cookie(main_page)

        for page_num in range(1, pages_to_scrape + 1):
            print(f"\n--- Scraping Page {page_num} of {pages_to_scrape} ---")

            try:
                if "verify-human" in main_page.url:
                    print("CAPTCHA detected, stopping.")
                    break

                complaint_list_selector = ".sc-1sm4sxr-0.iwOeoe"
                print(
                    f"Waiting for complaint list to load using selector: {complaint_list_selector}"
                )

                try:
                    print(f"Waiting for complaint list: {complaint_list_selector}")
                    main_page.wait_for_selector(complaint_list_selector, timeout=30000)
                    print("Complaint list loaded.")
                except TimeoutError:
                    print("Timeout while waiting for complaint list")
                    continue

                first_title_element = main_page.locator("h4.sc-1pe7b5t-1.bVKmkO").first
                old_title_text = first_title_element.inner_text()
                print(f"First title on this page '{old_title_text}'")

                html_content = main_page.content()
                soup = BeautifulSoup(html_content, "html.parser")
                complaint_containers = soup.select("div.sc-1pe7b5t-0.eJgBOc")
                if not complaint_containers:
                    print("No complaint containers found on this page.")
                    print(main_page.content()[:500])
                    break

                page_links = []
                for complaint in complaint_containers:
                    title_preview_tag = complaint.find(
                        "h4", class_="sc-1pe7b5t-1 bVKmkO"
                    )
                    title_preview = (
                        title_preview_tag.get_text(strip=True)
                        if title_preview_tag
                        else "No title"
                    )

                    description_preview_tag = complaint.find(
                        "p", class_="sc-1pe7b5t-2 eHoNfA"
                    )
                    description_preview = (
                        description_preview_tag.get_text(strip=True)
                        if description_preview_tag
                        else "No description"
                    )

                    date_preview_tag = complaint.find(
                        "span", class_="sc-1pe7b5t-5 dspDoZ"
                    )
                    date_preview = (
                        date_preview_tag.get_text(strip=True)
                        if date_preview_tag
                        else "No date"
                    )

                    link_tag = complaint.find("a", id="site_bp_lista_ler_reclamacao")

                    if (
                        title_preview
                        and description_preview
                        and date_preview
                        and link_tag
                    ):
                        print(f"Found complaint: {title_preview}")
                        page_links.append(
                            {
                                "url": base_url + link_tag["href"],
                                "title_preview": title_preview,
                                "desc_preview": description_preview,
                                "date_preview": date_preview,
                            }
                        )

                for i, link_info in enumerate(page_links):
                    print(f"  -> Navigating to complaint {i+1}: {link_info['url']}")
                    main_page.goto(link_info["url"], wait_until="domcontentloaded")
                    time.sleep(random.uniform(2, 4))

                    detail_soup = BeautifulSoup(main_page.content(), "html.parser")
                    details = fetch_complaint_info(detail_soup, i)

                    link_info.update(details)
                    all_complaints_data.append(link_info)

                    print("  <- Navigating back to the list page...")
                    main_page.go_back(wait_until="domcontentloaded")
                    time.sleep(random.uniform(4, 6))

                print(f"Finished scraping details for page {page_num}.")
                time.sleep(random.uniform(4, 6))

                if page_num == pages_to_scrape:
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

                    print("Waiting for new content to load...")
                    expect(first_title_element).not_to_have_text(
                        old_title_text, timeout=30000
                    )
                    print("page updated")

                    sleep_time = random.uniform(4, 6)
                    print(f"waiting for {sleep_time:.2f} seconds before next page...")
                    time.sleep(sleep_time)
                else:
                    print("could not find 'Next Page' button.")
                    break

            except TimeoutError:
                print(f"a timeout occurred in page {page_num}.")
                break
            except Exception as e:
                print(f"An error occurred while loading the page num {page_num}: {e}")
                return []

        print("\nScraping finished. Closing browser.")
        context.close()
        browser.close()
        return all_complaints_data


def fetch_segments():

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        main_page = context.new_page()

        print(f"Navigating to {SEGMENTS_PAGE_URL}...")
        main_page.goto(SEGMENTS_PAGE_URL, wait_until="domcontentloaded", timeout=60000)

        check_cookie(main_page)
        try:
            if "verify-human" in main_page.url:
                print("CAPTCHA detected, stopping.")

            segments_selector = "fab-wrapper"
            print(f"Waiting for segments to load using selector: {segments_selector}")

            try:
                main_page.get_by_test_id(segments_selector).wait_for(timeout=30000)
                print("Segments page loaded.")
            except TimeoutError:
                print("Timeout while waiting for segments page load.")
            except Exception as e:
                print(e)

            html_content = main_page.content()
            soup = BeautifulSoup(html_content, "html.parser")
            segment_containers = soup.select("div.rs-acordeon.rs-w-full.rs-h-fit")

            texts = soup.select("span.rs-text-base.rs-font-semibold.rs-text-left")
            segments = [text.get_text(strip=True) for text in texts if text]

            expand_buttons_locator = main_page.locator(
                "button[aria-controls$='-acordeon']"
            )
            button_count = expand_buttons_locator.count()
            print(f"Found {button_count} buttons to click.")

            if len(segments) != button_count:
                print("Segments and button_count are different. Check the index.")

            for i in range(button_count):
                print(f"Clicking button {i}.")
                button = expand_buttons_locator.nth(i)
                if button.is_visible():
                    button.hover()
                    time.sleep(random.uniform(0.5, 1.5))
                    button.click(delay=random.randint(50, 150))

            for segment_title in segments:
                print(f"Processing segment: {segment_title}")

                segment_button = main_page.locator(f"button[title$='{segment_title}']")
                print(segment_button)

                try:
                    segment_button.wait_for(state="visible", timeout=10000)

                    print("Found segment button. Clicking...")
                    segment_button.hover()
                    time.sleep(random.uniform(1.0, 2.5))
                    segment_button.click(delay=random.randint(1, 2))

                    time.sleep(50)

                    print("Navigating back to the segments list...")
                    main_page.go_back(wait_until="domcontentloaded")
                    print("Back on the list page.")
                except Exception as e:
                    print(f"An error occurred while processing '{segment_title}': {e}")
                    main_page.goto(SEGMENTS_PAGE_URL, wait_until="domcontentloaded")

        except Exception as e:
            print(e)


def execute():
    companies = [
        "mattel-do-brasil-fisher-price-barbie-hotwheels-polly-monster-high",
        "fun-divirta-se",
        "elka",
        "rca-entretenimento",
        "maral",
        "novabrink",
        "sunny-brinquedos-importacao-e-exportacao",
        "xalingo-brinquedos",
        "toyster-brinquedos",
        "mega-compras",
    ]

    results = []
    max_threads = min(len(companies), 16)

    with ThreadPoolExecutor(max_threads) as executor:
        futures = {
            executor.submit(scrape_complaints, company, 50): company
            for company in companies
        }
        for future in as_completed(futures):
            company = futures[future]
            try:
                data = future.result()
                if data:
                    results.extend(data)
                    print(f"Finished {company}, got {len(data)} complaints")
                else:
                    print(f"No data for {company}")
            except Exception as e:
                print(f"Error scraping {company}: {e}")

    if results:
        df = pd.DataFrame(results)
        base_output_file = f"reclameaqui_{len(companies)}_companies_complaints"

        csv_file = f"{base_output_file}.csv"
        df.to_csv(
            csv_file, index=False, encoding="utf-8-sig", sep=";", quoting=csv.QUOTE_ALL
        )
        print(f"Data saved successfully to {csv_file}")

        parquet_file = f"{base_output_file}.parquet"
        df.to_parquet(parquet_file, index=False)
        print(f"Data saved successfully to {parquet_file}")


if __name__ == "__main__":
    fetch_segments()
