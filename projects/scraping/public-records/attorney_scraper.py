from playwright.sync_api import sync_playwright
import csv
import json
import time
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent / "data-cleaning"))
from cleaning_utils import clean_name, clean_phone, deduplicate

OUTPUT_DIR = Path(__file__).parent


def scrape_attorneys(state="CA", max_pages=3):
    attorneys = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page()

        # Example: Scrape from a public attorney directory
        # This pattern works for most state bar websites
        base_url = "https://www.avvo.com/attorneys"
        
        page_num = 1
        while page_num <= max_pages:
            url = f"{base_url}/{state.lower()}/page/{page_num}" if page_num > 1 else f"{base_url}/{state.lower()}"
            
            print(f"Scraping page {page_num}: {url}")
            page.goto(url, timeout=30000)
            page.wait_for_selector(".search-result-item, .attorney-card, .lawyer-card", timeout=10000)
            
            # Extract attorney data
            cards = page.query_selector_all(".search-result-item, .attorney-card, .lawyer-card")
            
            if not cards:
                print(f"No attorneys found on page {page_num}")
                break
            
            print(f"  Found {len(cards)} attorneys")
            
            for card in cards:
                try:
                    name_el = card.query_selector("h3, .name, [itemprop='name']")
                    name = name_el.inner_text().strip() if name_el else ""
                    
                    practice_el = card.query_selector(".practice-area, .specialty")
                    practice_area = practice_el.inner_text().strip() if practice_el else ""
                    
                    phone_el = card.query_selector("a[href^='tel:'], .phone")
                    phone = ""
                    if phone_el:
                        href = phone_el.get_attribute("href") or ""
                        if href.startswith("tel:"):
                            phone = href.replace("tel:", "")
                        else:
                            phone = phone_el.inner_text().strip()
                    
                    location_el = card.query_selector(".location, .address")
                    location = location_el.inner_text().strip() if location_el else ""
                    
                    rating_el = card.query_selector(".rating, .stars")
                    rating = rating_el.inner_text().strip() if rating_el else ""
                    
                    if name:
                        attorneys.append({
                            "name": clean_name(name),
                            "practice_area": practice_area,
                            "phone": clean_phone(phone),
                            "location": location,
                            "rating": rating,
                        })
                except Exception as e:
                    print(f"  Error extracting attorney: {e}")
                    continue
            
            # Check for next page
            next_btn = page.query_selector("a[rel='next'], .next a, [class*='next'] a")
            if not next_btn:
                print("No more pages")
                break
            
            page_num += 1
            time.sleep(2)
        
        browser.close()
    
    return attorneys


def save_csv(data, filepath):
    if not data:
        print("No data to save")
        return
    
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    print(f"Saved {len(data)} attorneys to {filepath}")


def save_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(data)} attorneys to {filepath}")


if __name__ == "__main__":
    print("Scraping attorney listings with Playwright...\n")
    print("Note: Many attorney directories have anti-bot protection.")
    print("This scraper demonstrates the pattern for public directories.\n")
    
    attorneys = scrape_attorneys(state="CA", max_pages=3)
    
    # Deduplicate
    unique = deduplicate(attorneys, lambda a: f"{a['name']}|{a['phone']}")
    print(f"\nTotal: {len(attorneys)} attorneys")
    print(f"Unique: {len(unique)} attorneys")
    
    if unique:
        print(f"\nSample (first 3):")
        for a in unique[:3]:
            print(f"  {a['name']} | {a['practice_area'][:30]} | {a['phone']}")
        
        save_csv(unique, OUTPUT_DIR / "attorneys.csv")
        save_json(unique, OUTPUT_DIR / "attorneys.json")
    else:
        print("\nNo attorneys scraped. This is expected if the site has anti-bot protection.")
        print("For real projects, you would:")
        print("  1. Use proxies")
        print("  2. Add delays between requests")
        print("  3. Use residential proxies")
        print("  4. Try a different, more accessible site")
