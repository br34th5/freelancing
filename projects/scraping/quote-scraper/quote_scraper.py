import requests
from bs4 import BeautifulSoup
import json
import csv
import time
import random
from pathlib import Path

BASE_URL = "https://quotes.toscrape.com"
OUTPUT_DIR = Path(__file__).parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def scrape_page(url):
    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    quotes = []
    for quote_el in soup.select(".quote"):
        text = quote_el.select_one(".text").get_text(strip=True)
        author = quote_el.select_one(".author").get_text(strip=True)
        tags = quote_el.select_one(".keywords")["content"].split(",")

        quotes.append({
            "text": text,
            "author": author,
            "tags": tags,
        })

    next_btn = soup.select_one("li.next a")
    next_url = f"{BASE_URL}{next_btn['href']}" if next_btn else None

    return quotes, next_url


def scrape_all(start_url, delay=(1, 2)):
    all_quotes = []
    url = start_url
    page_num = 1

    while url:
        print(f"Scraping page {page_num}: {url}")
        quotes, next_url = scrape_page(url)
        all_quotes.extend(quotes)
        print(f"  Found {len(quotes)} quotes")

        url = next_url
        page_num += 1

        if url:
            wait = random.uniform(*delay)
            print(f"  Waiting {wait:.1f}s...")
            time.sleep(wait)

    return all_quotes


def save_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(data)} quotes to {filepath}")


def save_csv(data, filepath):
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "author", "tags"])
        writer.writeheader()
        for row in data:
            row["tags"] = ", ".join(row["tags"])
            writer.writerow(row)
    print(f"Saved {len(data)} quotes to {filepath}")


if __name__ == "__main__":
    quotes = scrape_all(f"{BASE_URL}/")
    save_json(quotes, OUTPUT_DIR / "quotes.json")
    save_csv(quotes, OUTPUT_DIR / "quotes.csv")
    print(f"\nDone! Total: {len(quotes)} quotes")
