import requests
import json
from pathlib import Path

API_BASE = "https://hacker-news.firebaseio.com/v0"
OUTPUT_DIR = Path(__file__).parent


def get_top_stories(count=90):
    resp = requests.get(f"{API_BASE}/topstories.json", timeout=10)
    resp.raise_for_status()
    return resp.json()[:count]


def get_item(item_id):
    resp = requests.get(f"{API_BASE}/item/{item_id}.json", timeout=10)
    resp.raise_for_status()
    return resp.json()


def scrape_hn(count=90):
    print(f"Fetching top {count} stories from HN API...")
    story_ids = get_top_stories(count)

    items = []
    for i, sid in enumerate(story_ids):
        data = get_item(sid)
        if not data or data.get("type") != "story":
            continue

        items.append({
            "id": sid,
            "title": data.get("title", ""),
            "url": data.get("url", f"https://news.ycombinator.com/item?id={sid}"),
            "points": data.get("score", 0),
            "author": data.get("by", ""),
            "comments": data.get("descendants", 0),
        })

        if (i + 1) % 30 == 0:
            print(f"  Fetched {i + 1}/{len(story_ids)} stories...")

    return items


if __name__ == "__main__":
    items = scrape_hn(count=90)

    items_sorted = sorted(items, key=lambda x: x["points"], reverse=True)

    print(f"\nTotal: {len(items_sorted)} stories")
    print(f"\nTop 10 by points:")
    for i, item in enumerate(items_sorted[:10], 1):
        print(f"  {i}. [{item['points']}pts] {item['title']}")
        print(f"     by {item['author']} | {item['comments']} comments")

    output_path = OUTPUT_DIR / "hackernews.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(items_sorted, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {output_path}")
