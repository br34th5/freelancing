"""Skelbiu.lt list spider — polite, robots-obeying, TLS-impersonating.

Politeness budget (from config.json):
  - concurrent_requests=1, per-domain=1 (serial)
  - download_delay = mean(delay_seconds) ≈ 3–5s between requests
  - AutoThrottle on, backs off on blocks
  - robots_txt_obey = True
  - max_pages_per_run kill-switch (default 80 ≈ 6 subcats × 5 + watched slugs)

Emits item dicts (one per listing) with observed_at timestamp. Storage (P3):
  database.save_items(items) — local Docker Postgres primary + Neon dual-write,
  with data/observations.jsonl kept as a raw crawl backup (re-loadable via
  `python3 -m database --from-jsonl data/observations.jsonl`).

Run as a module:
  python3 -m spider            # full crawl → DB (+ jsonl backup)
  python3 -m spider --pages 12 # override max pages (smoke test)

Pause/resume: crawldir=data/crawl_state holds checkpoints; SIGINT pauses gracefully.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from scrapling.fetchers import FetcherSession
from scrapling.spiders import Request, Spider
from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

from extract import parse_list_page
from database import save_items

DATA = Path(__file__).parent / "data"
CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())
OBS_PATH = DATA / "observations.jsonl"

# Lithuanian ad keywords wanted-ads lead with (belt-and-suspenders behind ?type=1)
_AD_PREFIXES = ("superku", "superka", "iesko", "ieško", "perku", "nuperku", "nupirkciau")


def _load_env() -> dict:
    env_path = Path(__file__).parent / ".env"
    out = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    out.update({k: v for k, v in os.environ.items() if k.startswith("PROXY")})
    return out


def _proxy_rotator(env: dict) -> ProxyRotator | None:
    """Build a rotator from env. Supported shapes (first match wins):
      PROXY_LIST=http://user:pass@host:port,http://...   (full URLs)
      PROXY_HOSTS=host1:port1,host2:port2  + PROXY_USER / PROXY_PASS (shared creds)
      PROXY_URL=http://user:pass@host:port (single)
    """
    entries: list[str] = []
    if env.get("PROXY_LIST"):
        entries.extend(x.strip() for x in env["PROXY_LIST"].split(",") if x.strip())
    elif env.get("PROXY_HOSTS"):
        user = env.get("PROXY_USER", "")
        pw = env.get("PROXY_PASS", "")
        auth = f"{user}:{pw}@" if user and pw else ""
        for hp in env["PROXY_HOSTS"].split(","):
            hp = hp.strip()
            if hp:
                scheme = hp if hp.startswith(("http://", "https://", "socks")) else f"http://{auth}{hp}"
                entries.append(scheme)
    elif env.get("PROXY_URL"):
        entries.append(env["PROXY_URL"].strip())
    if not entries:
        return None
    return ProxyRotator(entries)


class SkelbiuSpider(Spider):
    name = "skelbiu"
    allowed_domains = {"skelbiu.lt", "www.skelbiu.lt"}

    robots_txt_obey = True
    development_mode = False

    # politeness — fully serial
    concurrent_requests = 1
    concurrent_requests_per_domain = 1
    download_delay = float(sum(CONFIG["delay_seconds"]) / len(CONFIG["delay_seconds"]))

    # AutoThrottle
    autothrottle_enabled = True
    autothrottle_start_delay = 4.0
    autothrottle_max_delay = 30.0
    autothrottle_target_concurrency = 1.0
    autothrottle_block_backoff = True
    max_blocked_retries = 2

    logging_level = 20  # INFO
    log_file = str(DATA / "spider.log")

    def __init__(self, max_pages: int | None = None, **kw):
        self.max_pages = max_pages or CONFIG["max_pages_per_run"]
        self._pages_fetched = 0
        self._items_emitted = 0
        self._block_streak = 0          # consecutive CF-blocked responses
        self._max_block_streak = 0
        DATA.mkdir(exist_ok=True)
        super().__init__(crawldir=str(DATA / "crawl_state"), **kw)

    # ---- session: TLS impersonation + optional proxy -----------------------
    def configure_sessions(self, manager) -> None:
        env = _load_env()
        rotator = _proxy_rotator(env)
        kw: dict = dict(
            impersonate=CONFIG.get("fetch_impersonate", "chrome131"),
            timeout=30,
            retries=2,
            retry_delay=2,
            follow_redirects="safe",
        )
        if rotator is not None:
            kw["proxy_rotator"] = rotator
            self.logger.info("proxy rotator enabled")
        manager.add("default", FetcherSession(**kw))

    # ---- CF / block detection ----------------------------------------------
    async def is_blocked(self, response) -> bool:
        blocked = self._is_blocked_response(response)
        if blocked:
            self._block_streak += 1
            self._max_block_streak = max(self._max_block_streak, self._block_streak)
        return blocked

    def _is_blocked_response(self, response) -> bool:
        if response.status in (403, 503, 429):
            return True
        html = response.html_content or ""
        markers = (
            "cf-browser-verification",
            "challenge-platform",
            "Just a moment",
            "Checking your browser",
            "_cf_chl",
            "Enable JavaScript and cookies to continue",
        )
        return any(m in html for m in markers)

    async def retry_blocked_request(self, request: Request, response) -> Request:
        # rotate session (picks a new proxy if rotator present) and retry once
        await asyncio.sleep(random.uniform(*CONFIG["delay_seconds"]) * 3)
        return request

    # ---- start URLs ---------------------------------------------------------
    def _seed_urls(self) -> list[tuple[str, str]]:
        """(url, category) pairs — subcats first, then watched search slugs."""
        pairs: list[tuple[str, str]] = []
        type_q = f"?type={CONFIG['type_filter']}" if CONFIG.get("type_filter") else ""
        for cat, base in CONFIG["subcats"].items():
            pairs.append((f"{base.rstrip('/')}/{type_q}", cat))
        for slug in CONFIG.get("watched_slugs", []):
            slug = slug.strip().strip("/")
            if slug:
                pairs.append((f"https://www.skelbiu.lt/paieska/{slug}/{type_q}", f"watch:{slug}"))
        return pairs

    async def start_requests(self):
        for url, cat in self._seed_urls():
            yield Request(url, callback=self.parse, meta={"category": cat, "page_no": 1})

    # ---- parse --------------------------------------------------------------
    async def parse(self, response):
        if self._pages_fetched >= self.max_pages:
            self.logger.info("page budget reached (%d) — stopping", self.max_pages)
            return

        self._pages_fetched += 1
        cat = (response.meta or {}).get("category", "unknown")
        page_no = (response.meta or {}).get("page_no", 1)

        if response.status != 200:
            self.logger.warning("HTTP %s on %s (page %d)", response.status, response.url, page_no)
            return

        self._block_streak = 0  # a clean 200 breaks any CF-block streak

        listings, next_url = parse_list_page(
            response.html_content or "", response.url, cat
        )

        now = datetime.now(timezone.utc).isoformat()
        kept = 0
        for l in listings:
            # wanted-ad guard (type=1 should prevent, but titles can still slip)
            if l.title and l.title.lower().startswith(_AD_PREFIXES):
                continue
            item = {
                "item_id": l.item_id,
                "url": l.url,
                "title": l.title,
                "category": l.category,
                "price_eur": l.price_eur,
                "state": l.state,
                "time_to_sell_min": l.time_to_sell_min,
                "price_change_pct": l.price_change_pct,
                "location": l.location,
                "condition": l.condition,
                "observed_at": now,
                "source_page": response.url,
            }
            kept += 1
            self._items_emitted += 1
            yield item

        self.logger.info(
            "%s page %d: %d listings (%d kept), next=%s",
            cat, page_no, len(listings), kept, "yes" if next_url else "no",
        )

        # follow next page only if budget remains and we're under per-cat depth
        per_cat_limit = CONFIG["pages_per_subcat"] if not cat.startswith("watch:") else CONFIG.get("pages_per_watched_slug", 3)
        if next_url and self._pages_fetched < self.max_pages and page_no < per_cat_limit:
            yield Request(
                next_url,
                callback=self.parse,
                meta={"category": cat, "page_no": page_no + 1},
            )

    # ---- close: report ------------------------------------------------------
    async def on_close(self):
        self.logger.info(
            "crawl finished: %d pages, %d items", self._pages_fetched, self._items_emitted
        )


def run(max_pages: int | None = None) -> dict:
    """Run one crawl. Returns {exit_code, items, pages, max_block_streak,
    paused, completed, db_ok} for the daily pipeline's break checks."""
    spider = SkelbiuSpider(max_pages=max_pages)
    result = spider.start()
    items = list(result)
    db_ok = True
    if items:
        with OBS_PATH.open("a") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        try:
            stats = save_items(items)
            for role, s in stats.items():
                print(f"  {role}: {s.summary()}")
        except Exception as e:
            print(f"DB write failed (primary): {e}", file=sys.stderr)
            db_ok = False
    print(f"\n{len(items)} items appended to {OBS_PATH}")
    print(f"paused={result.paused} completed={result.completed}")
    return {
        "exit_code": 0 if (result.completed and db_ok) else 1,
        "items": len(items),
        "pages": spider._pages_fetched,
        "max_block_streak": spider._max_block_streak,
        "paused": result.paused,
        "completed": result.completed,
        "db_ok": db_ok,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=None, help="override max_pages_per_run")
    args = ap.parse_args()
    raise SystemExit(run(max_pages=args.pages)["exit_code"])
