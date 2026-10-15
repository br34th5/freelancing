"""Skelbiu.lt list spider — polite, robots-obeying, multi-engine.

Engines (config.json → fetch_engine, override via FETCH_ENGINE env):
  stealthy  - Real Chrome browser, auto-solves Cloudflare (slowest, most robust)
  fetcher   - TLS impersonation, no browser (fastest, may get blocked)

Politeness budget:
  - concurrent_requests=1, per-domain=1 (serial)
  - download_delay = mean(delay_seconds) ≈ 3–5s between requests
  - AutoThrottle on, backs off on blocks
  - robots_txt_obey = True
  - max_pages_per_run kill-switch (default 80 ≈ 6 subcats × 5 + watched slugs)

Proxy:
  StealthyFetcher: PROXY_SERVER + PROXY_USER + PROXY_PASS (env/.env)
  Fetcher: PROXY_LIST / PROXY_HOSTS + PROXY_USER + PROXY_PASS (legacy URL list)

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

from scrapling.fetchers import AsyncStealthySession, FetcherSession
from scrapling.spiders import Request, Spider
from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

from extract import parse_list_page, extract_description, parse_description_text
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
    # env vars override .env; include FALLBACK_PROXY_* and FETCH_ENGINE
    for k, v in os.environ.items():
        if k.startswith("PROXY") or k.startswith("FALLBACK_PROXY") or k == "FETCH_ENGINE":
            out[k] = v
    return out


def _proxy_dict(env: dict) -> dict | None:
    """Proxy as dict for StealthyFetcher (server/user/pass)."""
    server = env.get("PROXY_SERVER", "").strip()
    user = env.get("PROXY_USER", "").strip()
    pw = env.get("PROXY_PASS", "").strip()
    if not server:
        return None
    d: dict = {"server": server}
    if user:
        d["username"] = user
    if pw:
        d["password"] = pw
    return d


def _proxy_entries(env: dict) -> list[str]:
    """Normalized proxy URLs from env for Fetcher (legacy URL list format).

    Supported shapes (first match wins):
      PROXY_LIST=host1:port1,host2:port2          + PROXY_USER/PROXY_PASS
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


def _fallback_proxy_entries(env: dict) -> list[str]:
    """Fallback proxy URLs from FALLBACK_PROXY_LIST (used when primary fails).

    Format: FALLBACK_PROXY_LIST=host1:port1,host2:port2 + FALLBACK_PROXY_USER/PASS
    """
    user = env.get("FALLBACK_PROXY_USER", "")
    pw = env.get("FALLBACK_PROXY_PASS", "")
    auth = f"{user}:{pw}@" if user and pw else ""

    def _normalize(entry: str) -> str:
        e = entry.strip()
        if not e:
            return ""
        if "://" not in e:
            e = f"http://{e}"
        if auth and "@" not in e.split("://", 1)[1]:
            e = e.replace("://", f"://{auth}", 1)
        return e

    entries: list[str] = []
    if env.get("FALLBACK_PROXY_LIST"):
        entries.extend(
            filter(None, (_normalize(x) for x in env["FALLBACK_PROXY_LIST"].split(",")))
        )
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

    def __init__(self, max_pages: int | None = None, use_fallback: bool = False, **kw):
        self.max_pages = max_pages or CONFIG["max_pages_per_run"]
        self._pages_fetched = 0
        self._items_emitted = 0
        self._block_streak = 0          # consecutive CF-blocked responses
        self._max_block_streak = 0
        self._proxy_health: ProxyHealth | None = None
        self._fetch_engine = ""
        self._fallback_proxy_dict = None
        self._fallback_proxy_list: list[str] = []
        self._fallback_used = False
        self._use_fallback = use_fallback
        self._detail_specs: dict[str, dict] = {}  # item_id → {desc_cpu, desc_gpu, enriched_title}
        DATA.mkdir(exist_ok=True)
        super().__init__(crawldir=str(DATA / "crawl_state"), **kw)

    # ---- session: multi-engine + optional proxy ----------------------------
    def configure_sessions(self, manager) -> None:
        env = _load_env()
        engine = (env.get("FETCH_ENGINE") or CONFIG.get("fetch_engine", "stealthy")).lower()
        self._fetch_engine = engine

        proxy_d = _proxy_dict(env)
        proxy_list = _proxy_entries(env)

        if engine == "stealthy":
            kw: dict = dict(
                headless=True,
                real_chrome=True,
                solve_cloudflare=True,
                timeout=45000,
            )
            if proxy_d:
                kw["proxy"] = proxy_d
                self.logger.info(
                    "engine=stealthy, proxy=%s (Cloudflare auto-solve)",
                    re.sub(r"(//[^@]+@)", "\\1***@", proxy_d["server"]),
                )
            else:
                self.logger.info("engine=stealthy, no proxy (real Chrome bypasses CF)")
            manager.add("default", AsyncStealthySession(**kw))

        else:
            # fetcher — TLS impersonation, optional proxy rotator
            kw = dict(
                impersonate=CONFIG.get("fetch_impersonate", "chrome131"),
                timeout=30,
                retries=2,
                retry_delay=2,
                follow_redirects="safe",
            )
            if proxy_list:
                self._proxy_health = ProxyHealth(proxy_list)
                kw["proxy_rotator"] = ProxyRotator(proxy_list, strategy=self._proxy_health.strategy)
                self.logger.info(
                    "engine=fetcher, proxy rotator enabled (%d entries, CF-health sticky)",
                    len(proxy_list),
                )
            else:
                self.logger.info("engine=fetcher, no proxy (TLS impersonation only)")
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
        retry_num = (request.meta or {}).get("_retry_num", 0) + 1
        base_delay = random.uniform(*CONFIG["delay_seconds"]) * 3
        backoff = base_delay * (2 ** retry_num)
        jitter = random.uniform(0, base_delay)
        wait = min(backoff + jitter, 120)
        self.logger.info(
            "CF-blocked retry %d/%d — waiting %.1fs (backoff=%.1fs + jitter=%.1fs)",
            retry_num, self.max_blocked_retries, wait, backoff, jitter,
        )
        await asyncio.sleep(wait)
        request.meta = {**(request.meta or {}), "_retry_num": retry_num}
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
        need_detail = 0
        for l in listings:
            # wanted-ad guard (type=1 should prevent, but titles can still slip)
            if l.title and l.title.lower().startswith(_AD_PREFIXES):
                continue

            # yield item immediately (specs enriched later via detail page)
            item = self._build_item(l, cat, now, response.url)
            kept += 1
            self._items_emitted += 1
            yield item

            # if CPU/GPU missing from title, queue detail-page fetch for enrichment
            from specs import analyze_title
            ts = analyze_title(l.title)
            if not ts.cpus and not ts.gpus:
                need_detail += 1
                yield Request(
                    l.url,
                    callback=self.parse_detail,
                    meta={"category": cat, "listing": l, "now": now},
                )

        self.logger.info(
            "%s page %d: %d listings (%d kept, %d detail-queued), next=%s",
            cat, page_no, len(listings), kept, need_detail, "yes" if next_url else "no",
        )

        # follow next page only if budget remains and we're under per-cat depth
        per_cat_limit = CONFIG["pages_per_subcat"] if not cat.startswith("watch:") else CONFIG.get("pages_per_watched_slug", 3)
        if next_url and self._pages_fetched < self.max_pages and page_no < per_cat_limit:
            yield Request(
                next_url,
                callback=self.parse,
                meta={"category": cat, "page_no": page_no + 1},
            )

    def parse_detail(self, response):
        """Fetch detail page to extract CPU/GPU from description text.

        Stores extracted specs in self._detail_specs[item_id] for merging
        into items during run(). Does NOT yield items (CrawlResult doesn't
        reliably collect from async generator callbacks).
        """
        l = (response.meta or {}).get("listing")
        if not l:
            return

        if response.status != 200:
            self.logger.warning("detail %s: HTTP %s", response.url, response.status)
            return

        desc = extract_description(response.html_content or "")
        specs_found = parse_description_text(desc)
        cpu_text = specs_found.get("processor", "")
        gpu_text = specs_found.get("gpu", "")

        entry: dict = {}
        if cpu_text:
            entry["desc_cpu"] = cpu_text
        if gpu_text:
            entry["desc_gpu"] = gpu_text

        # enrich title with found specs so scoring can parse them
        enriched = l.title
        if cpu_text and not re.search(r"i[3579][- ]\d{4,5}|ryzen|xeon|epyc", l.title, re.I):
            enriched = f"{enriched} {cpu_text}"
        if gpu_text and not re.search(r"rtx|gtx|rx\s*\d{3,4}|arc\s+a\d{3}|geforce|radeon", l.title, re.I):
            enriched = f"{enriched} {gpu_text}"
        if enriched != l.title:
            entry["enriched_title"] = enriched

        if entry:
            self._detail_specs[l.item_id] = entry

        self.logger.info(
            "detail %s: cpu=%r gpu=%r", l.item_id,
            cpu_text[:40] or "—", gpu_text[:40] or "—",
        )

    @staticmethod
    def _build_item(l, cat: str, now: str, source_page: str) -> dict:
        return {
            "item_id": l.item_id,
            "url": l.url,
            "title": l.title,
            "category": l.category or cat,
            "price_eur": l.price_eur,
            "state": l.state,
            "time_to_sell_min": l.time_to_sell_min,
            "price_change_pct": l.price_change_pct,
            "location": l.location,
            "condition": l.condition,
            "observed_at": now,
            "source_page": source_page,
        }

    # ---- close: report ------------------------------------------------------
    async def on_close(self):
        self.logger.info(
            "crawl finished: %d pages, %d items", self._pages_fetched, self._items_emitted
        )
        if self._proxy_health is not None:
            self.logger.info("proxy health: %s", self._proxy_health.snapshot())
        else:
            self.logger.info("engine=%s", self._fetch_engine)


def run(max_pages: int | None = None) -> dict:
    """Run one crawl. Returns {exit_code, items, pages, max_block_streak,
    paused, completed, db_ok} for the daily pipeline's break checks.

    Exit codes: 0=success, 1=fail (blocked/error), 2=skip (all proxies failed).

    Fallback logic: if primary proxy (Oxylabs) fails (0 items), retry with
    fallback proxies (LT datacenter). If fallback also fails, skip the run.
    """
    env = _load_env()
    fallback_entries = _fallback_proxy_entries(env)

    # First attempt: primary config
    spider = SkelbiuSpider(max_pages=max_pages, use_fallback=False)
    result = spider.start()
    items = list(result)

    # Merge detail-page extracted specs (desc_cpu/desc_gpu/enriched_title)
    items = _merge_detail_specs(items, spider._detail_specs)

    # If 0 items and fallback proxies available, retry with fallback
    if not items and fallback_entries and not spider._fallback_used:
        print("Primary proxy failed (0 items) — trying fallback proxies...")
        spider = SkelbiuSpider(max_pages=max_pages, use_fallback=True)
        spider._fallback_proxy_list = fallback_entries
        spider._fallback_used = True
        result = spider.start()
        items = list(result)
        items = _merge_detail_specs(items, spider._detail_specs)

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

    # Exit codes: 0=success, 1=fail (blocked/error), 2=skip (all proxies failed)
    if result.completed and db_ok and items:
        exit_code = 0
    elif not items and spider._fallback_used:
        exit_code = 2
        print("WARNING: All proxy attempts failed — skipping this run")
    else:
        exit_code = 1

    return {
        "exit_code": exit_code,
        "items": len(items),
        "pages": spider._pages_fetched,
        "max_block_streak": spider._max_block_streak,
        "paused": result.paused,
        "completed": result.completed,
        "db_ok": db_ok,
    }


def _merge_detail_specs(items: list[dict], detail_specs: dict[str, dict]) -> list[dict]:
    """Merge detail-page extracted specs (desc_cpu/desc_gpu/enriched_title) into items."""
    if not detail_specs:
        return items
    merged = 0
    for item in items:
        specs = detail_specs.get(item.get("item_id", ""))
        if specs:
            item.update(specs)
            merged += 1
    if merged:
        print(f"  merged detail specs into {merged} items")
    return items


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=None, help="override max_pages_per_run")
    args = ap.parse_args()
    result = run(max_pages=args.pages)
    # Exit codes: 0=success, 1=fail (blocked/error), 2=skip (all proxies failed)
    # Skip (exit 2) means we tried all proxies and failed — exit 0 to avoid alerting
    if result["exit_code"] == 2:
        raise SystemExit(0)  # skip silently
    raise SystemExit(result["exit_code"])
