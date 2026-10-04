import csv
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "https://books.toscrape.com/"
OUTPUT_DIR = Path(__file__).parent


def scrape_books(max_pages=None):
    all_books = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page()
        page.goto(BASE_URL)
        page.wait_for_selector("article.product_pod")

        page_num = 1

        while True:
            if max_pages and page_num > max_pages:
                break

            page.wait_for_selector("article.product_pod")
            books = page.query_selector_all("article.product_pod")
            print(f"Page {page_num}: found {len(books)} books")

            for book in books:
                title_el = book.query_selector("h3 a")
                title = title_el.get_attribute("title")

                price_el = book.query_selector("p.price_color")
                price_text = price_el.inner_text()
                price = float(price_text.replace("£", "").strip())

                avail_el = book.query_selector("p.instock")
                availability_text = avail_el.inner_text().strip()
                avail_match = availability_text.split()[0]
                availability = int(avail_match) if avail_match.isdigit() else 0

                img_el = book.query_selector("div.image_container img")
                img_src = img_el.get_attribute("src")
                if img_src and not img_src.startswith("http"):
                    img_src = f"{BASE_URL}{img_src}"

                rating_el = book.query_selector("p.star-rating")
                rating_classes = rating_el.get_attribute("class").split()
                rating_map = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
                rating = next((rating_map[c] for c in rating_classes if c in rating_map), 0)

                link = title_el.get_attribute("href")
                if link and not link.startswith("http"):
                    link = f"{BASE_URL}{link}"

                all_books.append({
                    "title": title,
                    "price": price,
                    "availability": availability,
                    "rating": rating,
                    "image_url": img_src,
                    "product_url": link,
                })

            next_btn = page.query_selector("li.next a")
            if not next_btn:
                print("No more pages.")
                break

            next_href = next_btn.get_attribute("href")
            page.click("li.next a")
            page.wait_for_load_state("networkidle")
            page_num += 1

            time.sleep(1)

        browser.close()

    return all_books


def save_csv(data, filepath):
    if not data:
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    print(f"Saved {len(data)} books to {filepath}")


def save_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(data)} books to {filepath}")


if __name__ == "__main__":
    print("Scraping books with Playwright...\n")
    books = scrape_books()

    print(f"\nTotal books scraped: {len(books)}")
    print(f"\nSample (first 3):")
    for b in books[:3]:
        print(f"  £{b['price']:.2f} | ★{b['rating']} | {b['title'][:50]}")

    save_csv(books, OUTPUT_DIR / "books.csv")
    save_json(books, OUTPUT_DIR / "books.json")
