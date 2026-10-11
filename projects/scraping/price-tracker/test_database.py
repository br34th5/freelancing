"""DB tests for database.py against local Docker Postgres.
Uses a throwaway schema (pt_test_*) and drops it afterwards.
Run: python3 test_database.py"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from database import ensure_schema, load_items, connect

load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

URL = os.getenv("LOCAL_DATABASE_URL")
SCHEMA = f"pt_test_{os.getpid()}"
NOW = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"[{'ok ' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def item(item_id, state, observed_at, price=100.0):
    return {
        "item_id": item_id,
        "url": f"https://www.skelbiu.lt/skelbimai/{item_id}.html",
        "title": f"Test item {item_id}",
        "category": "laptops",
        "price_eur": price,
        "state": state,
        "time_to_sell_min": None,
        "price_change_pct": None,
        "location": "Vilnius",
        "condition": "Naudota",
        "observed_at": observed_at.isoformat(),
        "source_page": "https://www.skelbiu.lt/skelbimai/?type=1",
    }


def run():
    if not URL:
        print("SKIP: LOCAL_DATABASE_URL not set")
        return 0

    conn = connect(URL)
    try:
        ensure_schema(conn, schema=SCHEMA)

        # --- batch 1: two new listings, both active
        b1 = [item("1", "active", NOW), item("2", "active", NOW)]
        s1 = load_items(conn, b1, schema=SCHEMA)
        check("b1 obs inserted", s1.observations_inserted, 2)
        check("b1 listings upserted", s1.listings_upserted, 2)
        check("b1 transitions", s1.transitions_inserted, 0)

        # --- idempotent reload of the same batch
        s1b = load_items(conn, b1, schema=SCHEMA)
        check("reload obs", s1b.observations_inserted, 0)
        check("reload transitions", s1b.transitions_inserted, 0)

        # --- batch 2: item 1 sold (transition), item 2 price drop only
        t2 = NOW + timedelta(hours=5)
        sold = item("1", "sold", t2, price=95.0)
        sold["time_to_sell_min"] = 420  # sold badge carries time-on-market
        b2 = [sold, item("2", "active", t2, price=90.0)]
        s2 = load_items(conn, b2, schema=SCHEMA)
        check("b2 obs inserted", s2.observations_inserted, 2)
        check("b2 transitions", s2.transitions_inserted, 1)

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT from_state, to_state, price_eur, time_to_sell_min "
                f"FROM {SCHEMA}.state_transitions WHERE item_id='1'"
            )
            row = cur.fetchone()
        check("transition from→to", (row[0], row[1]), ("active", "sold"))
        check("transition price", row[2], 95.0)
        check("transition tts copied", row[3], 420)

        with conn.cursor() as cur:
            cur.execute(
                f"SELECT last_state, last_seen, condition FROM {SCHEMA}.listings "
                f"WHERE item_id='1'"
            )
            ls = cur.fetchone()
        check("listing 1 last_state", ls[0], "sold")
        check("listing 1 last_seen", ls[1], t2)
        check("listing 1 condition", ls[2], "Naudota")

        # --- item 2 still active, no transition, last_state unchanged
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT last_state FROM {SCHEMA}.listings WHERE item_id='2'"
            )
            check("listing 2 last_state", cur.fetchone()[0], "active")

        # --- stale backfill: old active obs for item 1 (already sold)
        old = NOW - timedelta(hours=2)
        s3 = load_items(conn, [item("1", "active", old)], schema=SCHEMA)
        check("stale obs inserted", s3.observations_inserted, 1)
        check("stale no transition", s3.transitions_inserted, 0)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT last_state, last_seen FROM {SCHEMA}.listings WHERE item_id='1'"
            )
            ls = cur.fetchone()
        check("stale no state regression", ls[0], "sold")
        check("stale no last_seen regression", ls[1], t2)

        # --- empty / malformed items
        s4 = load_items(conn, [{"item_id": ""}, item("3", "active", NOW)], schema=SCHEMA)
        check("blank id skipped", s4.skipped, 1)
        check("item 3 loaded", s4.listings_upserted, 1)

        print(f"\n{'PASS' if fails == 0 else f'{fails} FAILURES'}")
        return 1 if fails else 0
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
