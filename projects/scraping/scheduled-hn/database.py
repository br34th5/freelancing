#!/usr/bin/env python3
import logging
import os
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


def get_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")
    return psycopg2.connect(database_url)


def init_db():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
        logger.info("Database initialized")
    finally:
        conn.close()


def insert_stories(stories):
    if not stories:
        return 0

    conn = get_connection()
    try:
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
        conn.commit()
        logger.info(f"Inserted {len(stories)} stories into database")
        return len(stories)
    finally:
        conn.close()
