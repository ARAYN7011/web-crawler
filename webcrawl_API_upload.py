import os
import time
import requests
import random
import re
import io
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# ✅ CONFIG: Set up Chrome WebDriver
CHROMEDRIVER_PATH = r"C:\Users\Pranavi\PycharmProjects\PythonProject7\chromedriver-win64\chromedriver.exe"  # UPDATE THIS!

def get_chrome_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # Runs in headless mode
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--allow-running-insecure-content")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.5481.78 Safari/537.36"
    )
    service = Service(CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def get_company_name_from_url(url):
    """
    Extracts a company name from the website URL.
    Removes 'www.' if present and uses the first segment of the domain.
    """
    netloc = urlparse(url).netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    company = netloc.split('.')[0].capitalize()
    return company

def sanitize_filename(filename):
    """Remove invalid characters from file names."""
    return re.sub(r'[<>:"/\\|?*]', '', filename)

# API endpoint for uploading unprocessed reports
UPLOAD_API_URL = "https://api.reputdev.in/upload"  # UPDATE WITH ACTUAL ENDPOINT

def upload_unprocessed_report_api(pdf_url, company_name):
    """
    Downloads the PDF from the given URL and uploads it via the API.
    The API payload marks the file as 'unprocessed' and includes the dynamic company name.
    """
    try:
        # Download PDF using a proper User-Agent header
        response = requests.get(pdf_url, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        pdf_content = response.content
        # Remove any query parameters from the filename
        file_name = sanitize_filename(os.path.basename(pdf_url.split('?')[0]))
    except Exception as e:
        print(f"⚠️ Error downloading PDF from {pdf_url}: {e}")
        return

    payload = {
        "Report_Name": "unprocessed",
        "Company_Name": company_name,
        "DocumentURL": "",
        "FileName": file_name,
        "DocumentType": "CompanyReports"
    }
    files = {
        "file": (file_name, io.BytesIO(pdf_content), "application/pdf")
    }
    try:
        r = requests.post(UPLOAD_API_URL, data=payload, files=files, timeout=30)
        if r.status_code == 200:
            print(f"✅ Successfully uploaded unprocessed report: {file_name} (Company: {company_name})")
        else:
            print(f"⚠️ API request failed for {file_name}. Status: {r.status_code}, Response: {r.text}")
    except Exception as e:
        print(f"⚠️ Exception during API call for {file_name}: {e}")

def fetch_reports():
    driver = get_chrome_driver()

    # List of company URLs to crawl (add more URLs as needed)
    company_urls = [
        "https://www.shahi.co.in",
        "https://www.inditex.com"
    ]

    for company_url in company_urls:
        current_company = get_company_name_from_url(company_url)
        print(f"\n🔍 Crawling: {company_url} (Company: {current_company})")

        try:
            driver.get(company_url)
            time.sleep(random.randint(5, 10))  # Randomized wait to avoid detection
            print(f"✅ Successfully opened: {company_url}")
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, 1000);")
                time.sleep(2)
        except Exception as e:
            print(f"❌ Error loading {company_url}: {e}")
            continue

        report_links = []
        keywords = ['sustainability', 'esg', 'annual-report', 'environment', 'reports']
        exclude_keywords = ['career', 'jobs', 'team', 'contact', 'media', 'news']

        # Click buttons like "Read More" or "View More" to reveal hidden content
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for button in buttons:
            try:
                if any(text in button.text.lower() for text in ["read more", "view more", "explore", "details"]):
                    driver.execute_script("arguments[0].click();", button)
                    time.sleep(3)
            except:
                pass

        # Scan all links on the page
        all_links = driver.find_elements(By.TAG_NAME, "a")
        for link in all_links:
            href = link.get_attribute("href")
            if href and any(keyword in href.lower() for keyword in keywords) and not any(
                    excluded in href.lower() for excluded in exclude_keywords):
                report_links.append(href)

        if not report_links:
            print("🔍 No direct report links found, trying site search...")
            search_box = driver.find_elements(By.TAG_NAME, "input")
            if search_box:
                try:
                    search_box[0].send_keys("Sustainability Report")
                    search_box[0].send_keys(Keys.RETURN)
                    time.sleep(random.randint(5, 15))
                    search_results = driver.find_elements(By.TAG_NAME, "a")
                    for link in search_results:
                        href = link.get_attribute("href")
                        if href and any(keyword in href.lower() for keyword in keywords):
                            report_links.append(href)
                except:
                    pass

        # Process each report link found
        for report_url in report_links:
            if any(excluded in report_url.lower() for excluded in exclude_keywords):
                continue  # Skip non-report pages

            print(f"📄 Checking report page: {report_url}")
            try:
                driver.get(report_url)
                time.sleep(5)
            except Exception as e:
                print(f"❌ Error loading report page {report_url}: {e}")
                continue

            # Attempt to click any "Download" buttons to reveal PDF links
            download_buttons = driver.find_elements(By.TAG_NAME, "a")
            for button in download_buttons:
                try:
                    if "download" in button.text.lower():
                        driver.execute_script("arguments[0].click();", button)
                        time.sleep(3)
                except:
                    pass

            # Extract PDF links from the page
            pdf_links = [a.get_attribute("href") for a in driver.find_elements(By.TAG_NAME, "a")
                         if a.get_attribute("href") and ".pdf" in a.get_attribute("href")]

            for pdf_url in pdf_links:
                print(f"📄 Found PDF link: {pdf_url}")
                # Upload the PDF via API as an unprocessed report with the dynamic company name
                upload_unprocessed_report_api(pdf_url, current_company)

    driver.quit()
    print("✅ Scraping completed.")

# Run the Scraper
fetch_reports()
