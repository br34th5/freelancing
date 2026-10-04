#!/usr/bin/env python3
"""
Scheduled Hacker News scraper with proxy rotation.
Runs daily via GitHub Actions or cron.
"""

import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import requests

from proxy_rotator import ProxyRotator

# Load .env
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

OUTPUT_DIR = Path(__file__).parent / "data"
LOG_FILE = Path(__file__).parent / "hn_scraper.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def get_rotator():
    proxy_list = os.getenv("PROXY_LIST", "")
    user = os.getenv("PROXY_USER", "")
    password = os.getenv("PROXY_PASS", "")
    if proxy_list and user and password:
        return ProxyRotator(proxy_list, user, password)
    logger.warning("No proxy credentials found, running without proxies")
    return None


def get_top_stories(count=30):
    rotator = get_rotator()
    session = rotator.get_session() if rotator else requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    logger.info(f"Fetching top {count} stories...")
    try:
        response = session.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=10
        )
        response.raise_for_status()
        return response.json()[:count]
    except Exception as e:
        logger.error(f"Failed to fetch story IDs: {e}")
        raise


def get_story(item_id):
    rotator = get_rotator()
    session = rotator.get_session() if rotator else requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    try:
        response = session.get(
            f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.warning(f"Failed to fetch story {item_id}: {e}")
        return None


def main():
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info(f"Starting HN scraper at {start_time}")

    delay = random.randint(0, 60)
    logger.info(f"Waiting {delay}s to avoid peak time...")
    time.sleep(delay)

    try:
        story_ids = get_top_stories(50)

        stories = []
        for i, sid in enumerate(story_ids):
            story = get_story(sid)
            if story and story.get("type") == "story":
                stories.append({
                    "id": sid,
                    "title": story.get("title"),
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                    "points": story.get("score", 0),
                    "comments": story.get("descendants", 0),
                    "author": story.get("by"),
                    "scraped_at": datetime.now().isoformat()
                })

            if (i + 1) % 10 == 0:
                logger.info(f"  Fetched {i + 1}/{len(story_ids)}...")

        OUTPUT_DIR.mkdir(exist_ok=True)
        date_str = start_time.strftime("%Y-%m-%d")
        output_file = OUTPUT_DIR / f"hn_top_{date_str}.json"

        with open(output_file, 'w') as f:
            json.dump(stories, f, indent=2)

        logger.info(f"Saved {len(stories)} stories to {output_file}")

        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"Completed in {duration:.1f}s")
        if stories:
            logger.info(f"Top story: {stories[0]['title']} ({stories[0]['points']} pts)")

    except Exception as e:
        logger.error(f"Scraper failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
