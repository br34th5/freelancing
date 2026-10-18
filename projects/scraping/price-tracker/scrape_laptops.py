"""Manual laptops scraper — NO PROXY, runs only when invoked locally.

Laptops is excluded from the daily proxy-backed crawl because the category
trips aggressive Cloudflare protection that quickly burns the Oxylabs
monthly quota (verified 2026-10-18: full daily run rate-limited mid-crawl
when laptops was included). Use this script to refresh the laptops index
on demand, from your own IP, with no proxy.

Same parse + detail-enrichment logic as the main spider, but standalone
so a CF block on laptops doesn't take down the rest of the pipeline.

Cloudflare handoff:
  When CF blocks a page, a visible Chrome window opens at the same URL via
  patchright (`headless=False`). Solve the captcha in the window — the
  script polls the page every 5s and resumes as soon as real listings
  appear. The cookies from your solved session are saved to
  `data/laptops_cookies.json` and reused on subsequent FetcherSession
  requests (Cookie header), so you only solve once per run.

Usage:
    python3 -m scrape_laptops           # first 3 pages (~10 min, no proxy)
    python3 -m scrape_laptops --pages 5 # deeper crawl

Writes to:
    data/observations.jsonl            (raw backup, --from-jsonl to reload)
    data/laptops_cookies.json          (CF cookies after a manual solve)
    price_tracker.listings/observations (live DB, both primary + Neon)

No alerts: this is a manual run, so any "0 items" / CF block here is
operator error, not a pipeline break — nothing to wake anyone up for.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scrapling.fetchers import FetcherSession

from extract import parse_list_page, extract_description, parse_description_text
from specs import analyze_title
from database import save_items

CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())
DATA = Path(__file__).parent / "data"
OBS_PATH = DATA / "observations.jsonl"
COOKIES_PATH = DATA / "laptops_cookies.json"

LAPTOPS_URL = CONFIG["manual_laptops_url"].rstrip("/")
TYPE_Q = f"?type={CONFIG['type_filter']}" if CONFIG.get("type_filter") else ""

# Browser-like UA + language prefs — skelbiu checks these before serving.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ACCEPT_LANG = "lt-LT,lt;q=0.9,en-US;q=0.6,en;q=0.4"

# Marker that appears on the list page (skelbiu DOM: container div with this
# class holds every individual listing). Used to confirm the captcha was
# solved and real content rendered, not just a "Verifying..." spinner.
LIST_PAGE_MARKER = "standard-list-container"
# CF challenge page markers — any of these means the bot got blocked.
CF_MARKERS = (
    "cf-browser-verification",
    "challenge-platform",
    "Just a moment",
    "Checking your browser",
    "Verifying you are human",
    "cf_clearance",
)


def _cf_blocked(html: str) -> bool:
    return any(m in html for m in CF_MARKERS)


def _has_listings(html: str) -> bool:
    """True when the page shows actual listings (post-captcha)."""
    return LIST_PAGE_MARKER in html


def _delay_range() -> tuple[float, float]:
    """Per-request delay from config (slightly wider than the proxied run —
    without a proxy, hitting the same IP faster is exactly what triggers CF.
    """
    lo, hi = CONFIG["delay_seconds"]
    pad = 1.5
    return lo * pad, hi * pad


def _cookie_header(cookies: list[dict]) -> str:
    """Format a list of cookie dicts (patchright output) into a Cookie header
    value. cf_clearance + cf_bm carry the bulk of CF's auth.
    """
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value"))


def _load_saved_cookies() -> list[dict] | None:
    """Return saved cookies from a previous manual solve, if any."""
    if not COOKIES_PATH.exists():
        return None
    try:
        cookies = json.loads(COOKIES_PATH.read_text())
        return cookies if cookies else None
    except (json.JSONDecodeError, OSError):
        return None


def _solve_captcha_interactively(url: str, timeout_seconds: int = 300) -> list[dict] | None:
    """Open a visible Chrome via patchright for the user to solve CF.

    Polls every 5s for the list-page marker; returns cookies once real
    listings render, or None on timeout. Saves cookies to COOKIES_PATH so
    a follow-up run can reuse them without re-solving.
    """
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        print("  patchright not installed — can't open browser for handoff",
              file=sys.stderr)
        return None

    print(f"\n  ╔══════════════════════════════════════════════════════════╗")
    print(f"  ║ Cloudflare blocked the request.                          ║")
    print(f"  ║ A browser window is opening — solve the captcha there.   ║")
    print(f"  ║ The script resumes automatically once the page loads.    ║")
    print(f"  ╚══════════════════════════════════════════════════════════╝")
    print(f"  URL: {url}")
    print(f"  Timeout: {timeout_seconds // 60} min\n")

    with sync_playwright() as p:
        # headless=False shows the window; chromium keeps the same TLS
        # fingerprint as the FetcherSession's chrome131 impersonate.
        browser = p.chromium.launch(headless=False, channel="chromium")
        try:
            context = browser.new_context(
                user_agent=UA,
                locale="lt-LT",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Poll until the page renders actual listings. Each check costs
            # ~5s; 60 iterations × 5s = 5 min default.
            deadline = time.monotonic() + timeout_seconds
            attempt = 0
            while time.monotonic() < deadline:
                attempt += 1
                try:
                    html = page.content()
                    if _has_listings(html) and not _cf_blocked(html):
                        print(f"  ✓ Captcha solved (attempt {attempt}) — "
                              f"saving cookies and resuming")
                        cookies = context.cookies()
                        try:
                            COOKIES_PATH.write_text(json.dumps(cookies, indent=2))
                            print(f"  saved {len(cookies)} cookies → {COOKIES_PATH}")
                        except OSError as e:
                            print(f"  cookie save failed: {e}", file=sys.stderr)
                        return cookies
                except Exception as e:
                    # page.content() can fail if navigation interrupted; just
                    # keep polling until timeout
                    if attempt % 6 == 1:
                        print(f"  still waiting ({e})…")
                time.sleep(5)

            print(f"  ✗ Timeout after {timeout_seconds}s — giving up")
            return None
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _fetch_page(session: FetcherSession, url: str, cookie_header: str | None):
    """GET a page; return (status, html). Returns (None, None) on transport
    error so the caller can decide whether to retry or bail.
    """
    headers = {"User-Agent": UA, "Accept-Language": ACCEPT_LANG}
    if cookie_header:
        headers["Cookie"] = cookie_header
    try:
        resp = session.get(url, headers=headers)
    except Exception as e:
        print(f"  fetch error: {e}", file=sys.stderr)
        return None, None
    return resp.status, (resp.html_content or "")


def _fetch_detail(session: FetcherSession, url: str,
                  cookie_header: str | None) -> str | None:
    """GET one detail page; return its description text. None on failure / CF."""
    status, html = _fetch_page(session, url, cookie_header)
    if status != 200 or not html:
        return None
    if _cf_blocked(html):
        return None
    return extract_description(html)


def main() -> int:
    ap = argparse.ArgumentParser(description="Manual laptops scraper (no proxy)")
    ap.add_argument("--pages", type=int, default=3,
                    help="max pages to crawl (default 3)")
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    dmin, dmax = _delay_range()

    # TLS impersonation, no proxy. Old-style CF challenges can still get
    # through from a residential IP — _cf_blocked() handles that.
    session = FetcherSession(impersonate=CONFIG.get("fetch_impersonate", "chrome131"),
                             timeout=30, retries=1, follow_redirects="safe")

    # Reuse solved cookies from a prior run if present. Saves the user from
    # re-solving the captcha every time.
    saved_cookies = _load_saved_cookies()
    cookie_header = _cookie_header(saved_cookies) if saved_cookies else None
    if cookie_header:
        print(f"[laptops] reusing {len(saved_cookies)} saved cookies")

    page_no = 1
    page_url = f"{LAPTOPS_URL}/{TYPE_Q}"
    items: list[dict] = []
    captcha_solves = 0

    while page_url and page_no <= args.pages:
        print(f"[laptops] page {page_no}: {page_url}")
        status, html = _fetch_page(session, page_url, cookie_header)

        if status is None:
            print("  transport error — stopping")
            break
        if status != 200:
            print(f"  HTTP {status} — bailing")
            break

        if _cf_blocked(html):
            cookies = _solve_captcha_interactively(page_url)
            if not cookies:
                print("  captcha not solved — stopping scrape")
                break
            cookie_header = _cookie_header(cookies)
            captcha_solves += 1
            # retry the current page now that we have valid cookies
            status, html = _fetch_page(session, page_url, cookie_header)
            if status != 200 or not html or _cf_blocked(html):
                print("  post-solve fetch still blocked — stopping")
                break

        listings, next_url = parse_list_page(html, page_url, "laptops")
        if not listings:
            print("  no listings parsed — stopping")
            break
        print(f"  parsed {len(listings)} listings")

        now = datetime.now(timezone.utc).isoformat()
        for l in listings:
            item = {
                "item_id": l.item_id,
                "url": l.url,
                "title": l.title,
                "category": "laptops",
                "price_eur": l.price_eur,
                "state": l.state,
                "time_to_sell_min": l.time_to_sell_min,
                "price_change_pct": l.price_change_pct,
                "location": l.location,
                "condition": l.condition,
                "observed_at": now,
                "source_page": page_url,
            }

            # If EITHER cpu or gpu missing from title, fetch the detail
            # page and try to pull the model out of the prose description.
            ts = analyze_title(l.title)
            if not ts.cpus or not ts.gpus:
                time.sleep(random.uniform(dmin, dmax))
                desc = _fetch_detail(session, l.url, cookie_header)
                if desc is None and cookie_header is not None:
                    # detail page hit CF too — try one captcha handoff
                    cookies = _solve_captcha_interactively(l.url)
                    if cookies:
                        cookie_header = _cookie_header(cookies)
                        captcha_solves += 1
                        desc = _fetch_detail(session, l.url, cookie_header)
                if desc:
                    specs = parse_description_text(desc)
                    enriched = l.title
                    if specs.get("processor") and not ts.cpus:
                        item["desc_cpu"] = specs["processor"]
                        enriched = f"{enriched} {specs['processor']}"
                    if specs.get("gpu") and not ts.gpus:
                        item["desc_gpu"] = specs["gpu"]
                        enriched = f"{enriched} {specs['gpu']}"
                    if enriched != l.title:
                        item["enriched_title"] = enriched

            items.append(item)
            time.sleep(random.uniform(dmin, dmax))

        page_url = next_url
        page_no += 1
        if page_url and page_no <= args.pages:
            time.sleep(random.uniform(dmin * 2, dmax * 2))

    if not items:
        print("\nno items scraped — nothing to save")
        return 1

    # JSONL backup (raw, re-loadable)
    with OBS_PATH.open("a") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(items)} items to {OBS_PATH}")
    if captcha_solves:
        print(f"captcha handoff: solved {captcha_solves} time(s) this run")

    # DB upsert (primary + Neon copy)
    try:
        stats = save_items(items)
        for role, s in stats.items():
            print(f"  {role}: {s.summary()}")
    except Exception as e:
        print(f"  DB write failed: {e}", file=sys.stderr)
        return 2

    print(f"\nlaptops scrape done: {len(items)} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())