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
import re
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


def _proxy_entries(env: dict) -> list[str]:
    """Normalized proxy URLs from env. Supported shapes (first match wins):
      PROXY_LIST=host1:port1,host2:port2          + PROXY_USER/PROXY_PASS
                                                   (repo-standard shared-cred
                                                   form — creds are injected)
      PROXY_LIST=http://user:pass@host:port,...   (full URLs, used as-is)
      PROXY_HOSTS=host1:port1,host2:port2         + PROXY_USER/PROXY_PASS
      PROXY_URL=http://user:pass@host:port        (single)
    """
    user = env.get("PROXY_USER", "")
    pw = env.get("PROXY_PASS", "")
    auth = f"{user}:{pw}@" if user and pw else ""

    def _normalize(entry: str) -> str:
        e = entry.strip()
        if not e:
            return ""
        if "://" not in e:
            e = f"http://{e}"
        # inject shared creds when the URL itself carries none
        if auth and "@" not in e.split("://", 1)[1]:
            e = e.replace("://", f"://{auth}", 1)
        return e

    entries: list[str] = []
    if env.get("PROXY_LIST"):
        entries.extend(
            filter(None, (_normalize(x) for x in env["PROXY_LIST"].split(",")))
        )
    elif env.get("PROXY_HOSTS"):
        entries.extend(
            filter(None, (_normalize(x) for x in env["PROXY_HOSTS"].split(",")))
        )
    elif env.get("PROXY_URL"):
        e = _normalize(env["PROXY_URL"])
        if e:
            entries.append(e)
    return entries


# consecutive CF-blocks before a proxy is benched; whole-pool reset after all
CHAMP_FAIL_LIMIT = 2


class ProxyHealth:
    """CF-aware selection strategy for scrapling's ProxyRotator.

    Observed on skelbiu (2026-10-11 Actions runs): most pool exits get 403
    from Cloudflare, a few clear it — and clearance sticks per-IP for a
    while. So: probation cycles candidates; the first proxy that returns a
    clean list page becomes the *champion* and serves every subsequent
    request; consecutive blocks (CHAMP_FAIL_LIMIT) bench it and promote the
    next candidate. If the whole pool is benched, fails reset (CF may have
    cooled off) and probation restarts.

    The spider runs with concurrency=1, so the last proxy issued by the
    strategy is the one that served the response being classified — that is
    how report_ok/report_blocked attribute outcomes without races. A request
    that never reaches classification (connection error) counts as a fail
    when the next proxy is issued.
    """

    def __init__(self, entries: list[str]):
        self._entries = entries
        self.fails = [0] * len(entries)
        self.champion: int | None = None
        self._cursor = 0
        self._last: int | None = None
        self._last_reported = True

    # -- scrapling strategy signature: (proxies, index) -> (proxy, index) --
    def strategy(self, proxies, current_index):
        if self._last is not None and not self._last_reported:
            self._register_fail(self._last)   # errored out before classification
        if self.champion is not None:
            idx = self.champion
        else:
            idx = self._next_candidate(len(proxies))
        self._last, self._last_reported = idx, False
        return proxies[idx], idx

    def _next_candidate(self, size: int) -> int:
        for _ in range(size):
            idx = self._cursor % size
            self._cursor += 1
            if self.fails[idx] < CHAMP_FAIL_LIMIT:
                return idx
        # every proxy benched — give the pool a fresh probation wave
        self.fails = [0] * size
        return self._cursor % size

    def _register_fail(self, idx: int):
        self.fails[idx] += 1
        if self.champion == idx and self.fails[idx] >= CHAMP_FAIL_LIMIT:
            self.champion = None

    # -- outcomes, reported by the spider (idempotent per response) --------
    def report_ok(self):
        """Clean 200 that reached the parser — crown this proxy."""
        if self._last is None or self._last_reported:
            return
        self._last_reported = True
        self.fails[self._last] = 0
        self.champion = self._last

    def report_blocked(self):
        """CF challenge / 403 / 429 / 503 on the in-flight response."""
        if self._last is None or self._last_reported:
            return
        self._last_reported = True
        self._register_fail(self._last)

    def snapshot(self) -> str:
        """Redacted one-liner for logs (credentials never logged)."""
        champ = (re.sub(r"//[^@]+@", "//***@", self._entries[self.champion])
                 if self.champion is not None else None)
        return f"champion={champ} fails={self.fails}"


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
        self._proxy_health: ProxyHealth | None = None
        DATA.mkdir(exist_ok=True)
        super().__init__(crawldir=str(DATA / "crawl_state"), **kw)

    # ---- session: TLS impersonation + optional proxy -----------------------
    def configure_sessions(self, manager) -> None:
        env = _load_env()
        entries = _proxy_entries(env)
        kw: dict = dict(
            impersonate=CONFIG.get("fetch_impersonate", "chrome131"),
            timeout=30,
            retries=2,
            retry_delay=2,
            follow_redirects="safe",
        )
        if entries:
            self._proxy_health = ProxyHealth(entries)
            kw["proxy_rotator"] = ProxyRotator(entries, strategy=self._proxy_health.strategy)
            self.logger.info(
                "proxy rotator enabled (%d entries, CF-health sticky)", len(entries)
            )
        manager.add("default", FetcherSession(**kw))

    # ---- CF / block detection ----------------------------------------------
    async def is_blocked(self, response) -> bool:
        blocked = self._is_blocked_response(response)
        if self._proxy_health is not None:
            if blocked:
                self._proxy_health.report_blocked()
            # non-blocked outcomes are reported by parse() on a clean 200
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
        if self._proxy_health is not None:
            self._proxy_health.report_ok()  # this proxy cleared CF — crown it

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
        if self._proxy_health is not None:
            self.logger.info("proxy health: %s", self._proxy_health.snapshot())


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
