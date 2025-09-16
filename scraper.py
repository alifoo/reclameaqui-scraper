import time
import random
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError, expect
from playwright_stealth import Stealth
from bs4 import BeautifulSoup


def check_cookie(page):
    try:
        print("Checking for cookie banner...")
        accept_button = page.locator("#adopt-accept-all-button")
        accept_button.click(timeout=20000)
        print("Cookie banner accepted.")
    except TimeoutError:
        print("No cookie banner found or it was already dismissed.")


def scrape_first_page_complaints(company_name: str, pages_to_scrape: int):
    print(f"Starting to scrape {company_name}...")
    url = f"https://www.reclameaqui.com.br/empresa/{company_name}/lista-reclamacoes/"
    all_complaints_data = []

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        print(f"Navigating to {url}...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        check_cookie(page)

        for page_num in range(1, pages_to_scrape + 1):
            print(f"\n--- Scraping Page {page_num} of {pages_to_scrape} ---")

            try:
                if "verify-human" in page.url:
                    print("CAPTCHA detected, stopping.")
                    break

                complaint_list_selector = ".sc-1sm4sxr-0.iwOeoe"
                print(
                    f"Waiting for complaint list to load using selector: {complaint_list_selector}"
                )
                page.wait_for_selector(complaint_list_selector, timeout=30000)
                # time.sleep(3)

                first_title_element = page.locator("h4.sc-1pe7b5t-1.bVKmkO").first
                old_title_text = first_title_element.inner_text()
                print(f"First title on this page '{old_title_text}'")

                html_content = page.content()
                soup = BeautifulSoup(html_content, "html.parser")
                complaint_containers = soup.select("div.sc-1pe7b5t-0.eJgBOc")
                if not complaint_containers:
                    print("no complaint containers found.")
                    break

                complaints_data = []
                for complaint in complaint_containers:
                    title = complaint.find("h4", class_="sc-1pe7b5t-1 bVKmkO").get_text(
                        strip=True
                    )
                    description = complaint.find(
                        "p", class_="sc-1pe7b5t-2 eHoNfA"
                    ).get_text(strip=True)
                    date = complaint.find(
                        "span", class_="sc-1pe7b5t-5 dspDoZ"
                    ).get_text(strip=True)
                    complaints_data.append(
                        {"title": title, "description": description, "date": date}
                    )

                all_complaints_data.extend(complaints_data)
                print(f"Scraped {len(complaints_data)} complaints from this page.")

                if page_num == pages_to_scrape:
                    print("reached limit. stopping.")
                    break

                next_page_button = page.get_by_test_id("next-page-navigation-button")
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

                    sleep_time = random.uniform(5, 12)
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


if __name__ == "__main__":
    company_name = "mattel-do-brasil-fisher-price-barbie-hotwheels-polly-monster-high"
    scraped_data = scrape_first_page_complaints(company_name, 50)

    if scraped_data:
        df = pd.DataFrame(scraped_data)

        output_file = f"reclameaqui_{company_name}_complaints.csv"
        df.to_csv(output_file, index=False, encoding="utf-8-sig")

        print(f"Data saved successfully to {output_file}.")
