#!/usr/bin/env python3
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hn_stories (
    hn_id INTEGER NOT NULL,
    title TEXT,
    url TEXT,
    points INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    author TEXT,
    scraped_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (hn_id, scraped_at)
)
"""

INSERT_SQL = """
INSERT INTO hn_stories (hn_id, title, url, points, comments, author, scraped_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (hn_id, scraped_at) DO NOTHING
"""


def get_connection(url):
    return psycopg2.connect(url)


def init_db(url):
    conn = get_connection(url)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


def insert_stories(stories, url):
    if not stories:
        return 0

    conn = get_connection(url)
    try:
        inserted = 0
        with conn.cursor() as cur:
            for story in stories:
                cur.execute(INSERT_SQL, (
                    story["id"],
                    story["title"],
                    story["url"],
                    story["points"],
                    story["comments"],
                    story["author"],
                    datetime.fromisoformat(story["scraped_at"]),
                ))
                inserted += cur.rowcount
        conn.commit()
        return inserted
    finally:
        conn.close()


def save_stories(stories):
    """Insert into all configured databases.

    DATABASE_URL is primary — failures raise (triggers alert).
    LOCAL_DATABASE_URL is a best-effort copy — failures only warn.
    """
    primary = os.getenv("DATABASE_URL")
    local = os.getenv("LOCAL_DATABASE_URL")

    if not primary and not local:
        logger.info("No database configured, skipping database insert")
        return

    if primary:
        init_db(primary)
        n = insert_stories(stories, primary)
        logger.info(f"Inserted {n} new stories into primary database")

    if local:
        try:
            init_db(local)
            n = insert_stories(stories, local)
            logger.info(f"Inserted {n} new stories into local copy")
        except Exception as e:
            logger.warning(f"Local database copy failed (skipping): {e}")


DATA_DIR = Path(__file__).parent / "data"
REPO_ROOT = Path(__file__).parent.parent.parent.parent
REMOTE_DATA_PATH = "projects/scraping/scheduled-hn/data"


def _load_local_files():
    stories = []
    for f in sorted(DATA_DIR.glob("hn_top_*.json")):
        try:
            stories.extend(json.loads(f.read_text()))
        except Exception as e:
            logger.warning(f"Backfill: skipping unreadable {f.name}: {e}")
    return stories


def _load_remote_files():
    """Read data files from origin/main without touching the working tree."""
    stories = []
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=REPO_ROOT, capture_output=True, timeout=30, check=True,
        )
        ls = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "origin/main", "--", REMOTE_DATA_PATH],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=True,
        )
        for path in ls.stdout.splitlines():
            if not path.endswith(".json"):
                continue
            blob = subprocess.run(
                ["git", "show", f"origin/main:{path}"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=True,
            )
            stories.extend(json.loads(blob.stdout))
    except Exception as e:
        logger.warning(f"Backfill: remote repo source unavailable ({e})")
    return stories


def backfill_local():
    """Catch the local copy up with repo data: local data/*.json + origin/main.

    Idempotent (ON CONFLICT DO NOTHING) and never raises — safe to run anytime.
    """
    local = os.getenv("LOCAL_DATABASE_URL")
    if not local:
        return

    candidates = _load_local_files() + _load_remote_files()
    if not candidates:
        return

    seen = set()
    merged = []
    for s in candidates:
        key = (s.get("id"), s.get("scraped_at"))
        if key not in seen:
            seen.add(key)
            merged.append(s)

    try:
        init_db(local)
        inserted = insert_stories(merged, local)
        if inserted:
            logger.info(f"Backfill: local copy caught up, {inserted} new stories added")
    except Exception as e:
        logger.warning(f"Backfill failed (skipping): {e}")
