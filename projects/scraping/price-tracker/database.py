"""Storage layer — schema, idempotent loaders, state-transition detector.

Dual-write (same ON CONFLICT pattern as scheduled-hn/database.py):
  primary  = LOCAL_DATABASE_URL  (local Docker Postgres) — failures raise
  copy     = DATABASE_URL_NEON, falls back to DATABASE_URL (Neon) — best-effort warn

All tables live in schema `price_tracker` on both databases (no CREATE DATABASE
needed on either side; CREATE SCHEMA IF NOT EXISTS is enough).

Tables:
  listings           item_id PK — latest identity/state per listing
  observations       (item_id, observed_at) PK — one row per crawl sighting
  state_transitions  (item_id, detected_at) PK — active→sold / →reserved etc.
  cpu_scores         model PK — PassMark cpu_mark/st/mt/l3 (P4)
  gpu_scores         model PK — PassMark g3d/rank + TPU reco bracket (P4)
  listing_specs      item_id PK — scored derived fields incl. res_brackets/genre_tiers jsonb (P4)

Idempotency: reloading the same batch (or re-running a crawl window) inserts
nothing new. Stale backfills (observed_at < listings.last_seen) never regress
last_state and never emit spurious transitions.

CLI:
  python3 -m database --init                          # create schema+tables
  python3 -m database --from-jsonl data/observations.jsonl   # backfill
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(REPO_ROOT / ".env")
load_dotenv(Path(__file__).parent / ".env", override=True)  # local .env wins

SCHEMA = "price_tracker"

DDL = """
CREATE SCHEMA IF NOT EXISTS {s};

CREATE TABLE IF NOT EXISTS {s}.listings (
    item_id     TEXT PRIMARY KEY,
    url         TEXT,
    title       TEXT,
    category    TEXT,
    source      TEXT,
    location    TEXT,
    condition   TEXT,
    first_seen  TIMESTAMPTZ NOT NULL,
    last_seen   TIMESTAMPTZ NOT NULL,
    last_state  TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS {s}.observations (
    item_id          TEXT NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    price_eur        DOUBLE PRECISION,
    state            TEXT NOT NULL,
    time_to_sell_min INTEGER,
    price_change_pct DOUBLE PRECISION,
    PRIMARY KEY (item_id, observed_at)
);

CREATE TABLE IF NOT EXISTS {s}.state_transitions (
    item_id           TEXT NOT NULL,
    detected_at       TIMESTAMPTZ NOT NULL,
    from_state        TEXT,
    to_state          TEXT NOT NULL,
    price_eur         DOUBLE PRECISION,
    time_to_sell_min  INTEGER,
    PRIMARY KEY (item_id, detected_at)
);

CREATE INDEX IF NOT EXISTS idx_{s}_obs_item ON {s}.observations (item_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_{s}_trans_at ON {s}.state_transitions (detected_at DESC);

CREATE TABLE IF NOT EXISTS {s}.cpu_scores (
    model       TEXT PRIMARY KEY,
    cpu_mark    DOUBLE PRECISION,
    st_mark     DOUBLE PRECISION,
    mt_pct      DOUBLE PRECISION,
    l3_mb       DOUBLE PRECISION,
    rank        INTEGER,
    scraped_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS {s}.gpu_scores (
    model             TEXT PRIMARY KEY,
    g3d_mark          DOUBLE PRECISION,
    rank              INTEGER,
    tpu_reco_res      TEXT,
    tpu_secondary_res TEXT,
    vram_gb           DOUBLE PRECISION,
    scraped_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS {s}.listing_specs (
    item_id           TEXT PRIMARY KEY,
    device_class      TEXT,
    cpu_model         TEXT,
    cpu_score         DOUBLE PRECISION,
    gpu_model         TEXT,
    gpu_score         DOUBLE PRECISION,
    canonical_model   TEXT,
    res_brackets      JSONB,
    genre_tiers       JSONB,
    bottleneck        TEXT,
    combined_score    DOUBLE PRECISION,
    tier              TEXT,
    value_score       DOUBLE PRECISION,
    spec_confidence   TEXT,
    scored_at         TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_{s}_specs_tier ON {s}.listing_specs (tier);
"""

INSERT_OBS = """
INSERT INTO {s}.observations
    (item_id, observed_at, price_eur, state, time_to_sell_min, price_change_pct)
VALUES %s
ON CONFLICT (item_id, observed_at) DO NOTHING
"""

UPSERT_LISTING = """
INSERT INTO {s}.listings
    (item_id, url, title, category, source, location, condition,
     first_seen, last_seen, last_state)
VALUES %s
ON CONFLICT (item_id) DO UPDATE SET
    url        = EXCLUDED.url,
    title      = EXCLUDED.title,
    category   = EXCLUDED.category,
    source     = EXCLUDED.source,
    first_seen = LEAST({s}.listings.first_seen, EXCLUDED.first_seen),
    last_seen  = GREATEST({s}.listings.last_seen, EXCLUDED.last_seen),
    -- only the newest sighting may move the denormalized state
    last_state = CASE WHEN EXCLUDED.last_seen >= {s}.listings.last_seen
                      THEN EXCLUDED.last_state ELSE {s}.listings.last_state END,
    location   = CASE WHEN EXCLUDED.last_seen >= {s}.listings.last_seen
                      THEN EXCLUDED.location ELSE {s}.listings.location END,
    condition  = CASE WHEN EXCLUDED.last_seen >= {s}.listings.last_seen
                      THEN EXCLUDED.condition ELSE {s}.listings.condition END
"""

INSERT_TRANS = """
INSERT INTO {s}.state_transitions
    (item_id, detected_at, from_state, to_state, price_eur, time_to_sell_min)
VALUES %s
ON CONFLICT (item_id, detected_at) DO NOTHING
"""

UPSERT_CPU_SCORE = """
INSERT INTO {s}.cpu_scores (model, cpu_mark, st_mark, mt_pct, l3_mb, rank, scraped_at)
VALUES %s
ON CONFLICT (model) DO UPDATE SET
    cpu_mark = EXCLUDED.cpu_mark, st_mark = EXCLUDED.st_mark,
    mt_pct = EXCLUDED.mt_pct, l3_mb = EXCLUDED.l3_mb,
    rank = EXCLUDED.rank, scraped_at = EXCLUDED.scraped_at
"""

UPSERT_GPU_SCORE = """
INSERT INTO {s}.gpu_scores
    (model, g3d_mark, rank, tpu_reco_res, tpu_secondary_res, vram_gb, scraped_at)
VALUES %s
ON CONFLICT (model) DO UPDATE SET
    g3d_mark = EXCLUDED.g3d_mark, rank = EXCLUDED.rank,
    tpu_reco_res = EXCLUDED.tpu_reco_res,
    tpu_secondary_res = EXCLUDED.tpu_secondary_res,
    vram_gb = EXCLUDED.vram_gb, scraped_at = EXCLUDED.scraped_at
"""

UPSERT_LISTING_SPEC = """
INSERT INTO {s}.listing_specs
    (item_id, device_class, cpu_model, cpu_score, gpu_model, gpu_score,
     canonical_model, res_brackets, genre_tiers, bottleneck, combined_score,
     tier, value_score, spec_confidence, scored_at)
VALUES %s
ON CONFLICT (item_id) DO UPDATE SET
    device_class = EXCLUDED.device_class, cpu_model = EXCLUDED.cpu_model,
    cpu_score = EXCLUDED.cpu_score, gpu_model = EXCLUDED.gpu_model,
    gpu_score = EXCLUDED.gpu_score, canonical_model = EXCLUDED.canonical_model,
    res_brackets = EXCLUDED.res_brackets, genre_tiers = EXCLUDED.genre_tiers,
    bottleneck = EXCLUDED.bottleneck, combined_score = EXCLUDED.combined_score,
    tier = EXCLUDED.tier, value_score = EXCLUDED.value_score,
    spec_confidence = EXCLUDED.spec_confidence, scored_at = EXCLUDED.scored_at
"""


@dataclass
class LoadStats:
    observations_inserted: int = 0
    listings_upserted: int = 0
    transitions_inserted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def merged(self, other: "LoadStats") -> "LoadStats":
        return LoadStats(
            self.observations_inserted + other.observations_inserted,
            self.listings_upserted + other.listings_upserted,
            self.transitions_inserted + other.transitions_inserted,
            self.skipped + other.skipped,
            self.errors + other.errors,
        )

    def summary(self) -> str:
        return (
            f"{self.observations_inserted} observations, "
            f"{self.listings_upserted} listings, "
            f"{self.transitions_inserted} transitions"
            + (f", {len(self.errors)} errors" if self.errors else "")
        )


def connect(url: str):
    return psycopg2.connect(url)


def ensure_schema(conn, schema: str = SCHEMA) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL.format(s=schema))
    conn.commit()


def _as_dt(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize(items: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Parse timestamps; return (all rows, newest row per item_id)."""
    rows: list[dict] = []
    newest: dict[str, dict] = {}
    for raw in items:
        item_id = str(raw.get("item_id", "")).strip()
        if not item_id:
            continue
        row = dict(raw)
        row["item_id"] = item_id
        row["observed_at"] = _as_dt(raw["observed_at"])
        row["state"] = raw.get("state") or "active"
        rows.append(row)
        cur = newest.get(item_id)
        if cur is None or row["observed_at"] >= cur["observed_at"]:
            newest[item_id] = row
    return rows, newest


def load_items(conn, items: list[dict], schema: str = SCHEMA) -> LoadStats:
    """Idempotent batch load into one connection. Detects state transitions
    against the pre-existing listings.last_state (read before any writes)."""
    stats = LoadStats()
    rows, newest = _normalize(items)
    stats.skipped = len(items) - len(rows)
    if not rows:
        return stats

    ids = sorted(newest)
    with conn.cursor() as cur:
        # 1) snapshot previous state (must run BEFORE the listing upserts)
        cur.execute(
            f"SELECT item_id, last_state, last_seen FROM {schema}.listings WHERE item_id = ANY(%s)",
            (ids,),
        )
        prev = {r[0]: {"state": r[1], "last_seen": r[2]} for r in cur.fetchall()}

        # 2) observations (PK-conflict = already seen, skipped)
        obs_rows = [
            (r["item_id"], r["observed_at"], r.get("price_eur"),
             r["state"], r.get("time_to_sell_min"), r.get("price_change_pct"))
            for r in rows
        ]
        execute_values(cur, INSERT_OBS.format(s=schema), obs_rows, page_size=500)
        stats.observations_inserted = cur.rowcount

        # 3) listings upsert (latest sighting per item)
        list_rows = [
            (r["item_id"], r.get("url"), r.get("title"), r.get("category"),
             r.get("source_page"), r.get("location"), r.get("condition"),
             r["observed_at"], r["observed_at"], r["state"])
            for r in newest.values()
        ]
        execute_values(cur, UPSERT_LISTING.format(s=schema), list_rows, page_size=500)
        stats.listings_upserted = cur.rowcount

        # 4) transitions: state changed vs pre-existing AND sighting is not stale
        trans_rows = []
        for item_id, r in newest.items():
            p = prev.get(item_id)
            if not p or p["state"] == r["state"]:
                continue
            if r["observed_at"] < p["last_seen"]:
                continue  # stale backfill — never regress / re-emit
            trans_rows.append(
                (item_id, r["observed_at"], p["state"], r["state"],
                 r.get("price_eur"), r.get("time_to_sell_min"))
            )
        if trans_rows:
            execute_values(cur, INSERT_TRANS.format(s=schema), trans_rows, page_size=500)
            stats.transitions_inserted = cur.rowcount

    conn.commit()
    return stats


def read_jsonl(path: Path | str) -> list[dict]:
    items = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as e:
            logger.warning("skipping unreadable jsonl line: %s", e)
    return items


def _jsonb(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _load_to_all(upsert_sql, rows: list[tuple], label: str) -> dict[str, int]:
    """Upsert score/spec rows into every configured DB (primary raises)."""
    if not rows:
        return {}
    out: dict[str, int] = {}
    for role, url in _targets():
        conn = connect(url)
        try:
            ensure_schema(conn)
            with conn.cursor() as cur:
                execute_values(cur, upsert_sql.format(s=SCHEMA), rows, page_size=500)
                out[role] = cur.rowcount
            conn.commit()
            logger.info("%s: %d %s rows", role, out[role], label)
        finally:
            conn.close()
    return out


def save_cpu_scores(rows: list[tuple]) -> dict[str, int]:
    """(model, cpu_mark, st_mark, mt_pct, l3_mb, rank, scraped_at)"""
    return _load_to_all(UPSERT_CPU_SCORE, rows, "cpu_scores")


def save_gpu_scores(rows: list[tuple]) -> dict[str, int]:
    """(model, g3d_mark, rank, tpu_reco_res, tpu_secondary_res, vram_gb, scraped_at)"""
    return _load_to_all(UPSERT_GPU_SCORE, rows, "gpu_scores")


def save_listing_specs(rows: list[tuple]) -> dict[str, int]:
    """(item_id, device_class, cpu_model, cpu_score, gpu_model, gpu_score,
     canonical_model, res_brackets, genre_tiers, bottleneck, combined_score,
     tier, value_score, spec_confidence, scored_at) — jsonb fields passed as
     JSON strings via _jsonb()."""
    return _load_to_all(UPSERT_LISTING_SPEC, rows, "listing_specs")


def _primary_conn():
    """Open an ensured connection to the primary (local) DB. Raises if unset."""
    for role, url in _targets():
        if role == "primary":
            conn = connect(url)
            ensure_schema(conn)
            return conn
    raise RuntimeError("no primary database configured (LOCAL_DATABASE_URL)")


def _targets() -> list[tuple[str, str]]:
    """(role, url) pairs — primary first. Neon falls back to DATABASE_URL.

    When no local DB is configured (e.g. GitHub Actions), Neon IS the
    primary: failures raise instead of being warn-only.
    """
    out: list[tuple[str, str]] = []
    local = os.getenv("LOCAL_DATABASE_URL")
    neon = os.getenv("DATABASE_URL_NEON") or os.getenv("DATABASE_URL")
    if local:
        out.append(("primary", local))
        if neon:
            out.append(("neon", neon))
    elif neon:
        out.append(("primary", neon))
    return out


def save_items(items: list[dict]) -> dict[str, LoadStats]:
    """Insert into all configured databases. Primary (local) failures raise;
    the Neon copy is best-effort and only warns."""
    targets = _targets()
    if not targets:
        logger.info("no database configured, skipping storage")
        return {}

    results: dict[str, LoadStats] = {}
    for role, url in targets:
        if role == "primary":
            conn = connect(url)
            try:
                ensure_schema(conn)
                results[role] = load_items(conn, items)
                logger.info("primary: %s", results[role].summary())
            finally:
                conn.close()
        else:
            try:
                conn = connect(url)
                try:
                    ensure_schema(conn)
                    results[role] = load_items(conn, items)
                    logger.info("neon copy: %s", results[role].summary())
                finally:
                    conn.close()
            except Exception as e:
                logger.warning("neon copy failed (skipping): %s", e)
    return results


def init_all() -> None:
    for role, url in _targets():
        try:
            conn = connect(url)
            try:
                ensure_schema(conn)
                logger.info("%s: schema ready (%s)", role, SCHEMA)
            finally:
                conn.close()
        except Exception as e:
            if role == "primary":
                raise
            logger.warning("neon init failed (skipping): %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="price-tracker storage")
    ap.add_argument("--init", action="store_true", help="create schema + tables")
    ap.add_argument("--from-jsonl", metavar="PATH",
                    help="backfill observations from a jsonl file")
    args = ap.parse_args()

    if args.init:
        init_all()
        print(f"schema '{SCHEMA}' ready on: {', '.join(r for r, _ in _targets()) or '(none)'}")

    if args.from_jsonl:
        items = read_jsonl(args.from_jsonl)
        results = save_items(items)
        for role, s in results.items():
            print(f"  {role}: {s.summary()}")
        if not results:
            print("no database configured — nothing loaded")

    if not args.init and not args.from_jsonl:
        ap.print_help()
