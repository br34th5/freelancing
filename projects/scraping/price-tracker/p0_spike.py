"""P0 fetch spike — skelbiu.lt + TechPowerUp bot-check.

Tests three fetch strategies on skelbiu.lt (plain / Fetcher-impersonate /
StealthyFetcher) and TechPowerUp, parses sold-badge + price selectors,
prints a go/no-go table.

Run: python3 p0_spike.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

import requests as plain_requests
from scrapling import fetchers

SKELBIU_URLS = [
    ("list-laptops", "https://www.skelbiu.lt/skelbimai/kompiuterija/kompiuteriai/nesiojami-kompiuteriai/"),
    ("list-cpu", "https://www.skelbiu.lt/skelbimai/kompiuterija/kompiuteriu-komponentai/stacionariems-kompiuteriams/procesoriai-cpu/"),
    ("search-rtx4070", "https://www.skelbiu.lt/paieska/rtx-4070/"),
]

TPU_URLS = [
    ("gpu-specs", "https://www.techpowerup.com/gpu-specs/geforce-rtx-4070.c4187"),
    ("vga-hierarchy", "https://www.techpowerup.com/reviews/NVIDIA/GeForce_RTX_5070_Ti/"),
]

# Selectors from plan research
SEL_ITEM = "a.standard-list-item[data-item-id]"
SEL_PRICE = ".price-item > .price"
SEL_SOLD = ".top-right .item.sold"
SEL_RESERVED = ".item.reserved-icon"
SEL_PAGINATION = 'a.pagination_link[rel="next"]'


@dataclass
class FetchResult:
    label: str
    strategy: str
    status: int | str = "?"
    bytes: int = 0
    cf_blocked: bool = False
    items: int = 0
    sold_badge: int = 0
    reserved_badge: int = 0
    price_sample: str = ""
    title_sample: str = ""
    notes: list[str] = field(default_factory=list)


def _looks_like_cf(html: str, status: int) -> bool:
    markers = (
        "cf-browser-verification",
        "challenge-platform",
        "Just a moment",
        "Checking your browser",
        "cf_chl",
        "_cf_chl",
        "Enable JavaScript and cookies to continue",
    )
    return status in (403, 503) or any(m in html for m in markers)


def fetch_plain(url: str) -> tuple[int, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,lt;q=0.8",
    }
    r = plain_requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    return r.status_code, r.text


def fetch_impersonate(url: str, impersonate: str = "chrome131") -> tuple[int, str]:
    resp = fetchers.Fetcher.get(url, impersonate=impersonate, timeout=25, follow_redirects=True)
    return resp.status, resp.html_content or ""


def fetch_stealthy(url: str) -> tuple[int, str]:
    resp = fetchers.StealthyFetcher.fetch(url, headless=True, timeout=40)
    return resp.status, resp.html_content or ""


def parse_skelbiu(html: str, result: FetchResult) -> None:
    from scrapling.parser import Selector

    page = Selector(html)
    items = page.css(SEL_ITEM)
    result.items = len(items)
    result.sold_badge = len(page.css(SEL_SOLD))
    result.reserved_badge = len(page.css(SEL_RESERVED))

    prices = [el.text.strip() for el in page.css(SEL_PRICE)]
    result.price_sample = "; ".join(prices[:5])

    if items:
        t = items[0].get_all_text(strip=True)
        result.title_sample = " ".join(t)[:90]

    # dedupe note: title/price render twice (collapsed + expanded)
    if result.items and result.sold_badge == 0 and result.items > 20:
        result.notes.append("no sold badges visible on this page (may just be timing)")


def parse_tpu(html: str, result: FetchResult) -> None:
    from scrapling.parser import Selector

    page = Selector(html)
    # TPU spec table marker
    if "Clock Specs" in html or "GPU Model" in html or "Memory Size" in html:
        result.items = 1
        result.notes.append("spec table content present")
    if "reviews" in html.lower() and "fps" in html.lower():
        result.notes.append("fps/review content present")


def run_test(label: str, url: str, strategy: str, fn, parser) -> FetchResult:
    r = FetchResult(label=label, strategy=strategy)
    t0 = time.time()
    try:
        status, html = fn(url)
        r.status = status
        r.bytes = len(html)
        r.cf_blocked = _looks_like_cf(html, status)
        if not r.cf_blocked:
            parser(html, r)
        else:
            r.notes.append("CF challenge detected")
    except Exception as e:  # noqa: BLE001
        r.status = "ERR"
        r.notes.append(f"{type(e).__name__}: {e}")
    r.notes.append(f"{time.time() - t0:.1f}s")
    return r


def print_table(results: list[FetchResult]) -> None:
    print(f"\n{'label':<22} {'strategy':<14} {'status':<6} {'bytes':>8} "
          f"{'CF?':<4} {'items':>5} {'sold':>4} {'resv':>4}  notes")
    print("-" * 110)
    for r in results:
        print(f"{r.label:<22} {r.strategy:<14} {str(r.status):<6} {r.bytes:>8} "
              f"{'YES' if r.cf_blocked else 'ok':<4} {r.items:>5} {r.sold_badge:>4} "
              f"{r.reserved_badge:>4}  {'; '.join(r.notes)[:60]}")
        if r.price_sample:
            print(f"{'':<22} prices: {r.price_sample[:80]}")
        if r.title_sample:
            print(f"{'':<22} title:  {r.title_sample}")


def main() -> int:
    results: list[FetchResult] = []

    print("=== skelbiu.lt — plain HTTP ===")
    for label, url in SKELBIU_URLS[:1]:  # 1 URL is enough to detect CF
        results.append(run_test(label, url, "plain", fetch_plain, parse_skelbiu))
        time.sleep(3)
    print_table(results)

    need_impersonate = any(r.cf_blocked for r in results)
    if not need_impersonate:
        print("\nplain HTTP worked — skipping impersonate/stealthy")
    else:
        print("\n=== skelbiu.lt — Fetcher impersonate chrome131 ===")
        imp_results = []
        for label, url in SKELBIU_URLS:
            imp_results.append(run_test(label, url, "impersonate", fetch_impersonate, parse_skelbiu))
            time.sleep(4)
        results.extend(imp_results)
        print_table(imp_results)

        if any(r.cf_blocked or r.status == "ERR" for r in imp_results):
            print("\n=== skelbiu.lt — StealthyFetcher (camoufox) ===")
            st_results = []
            for label, url in SKELBIU_URLS[:2]:
                st_results.append(run_test(label, url, "stealthy", fetch_stealthy, parse_skelbiu))
                time.sleep(5)
            results.extend(st_results)
            print_table(st_results)

    print("\n=== TechPowerUp — impersonate ===")
    tpu_results = []
    for label, url in TPU_URLS:
        tpu_results.append(run_test(label, url, "impersonate", fetch_impersonate, parse_tpu))
        time.sleep(4)
    if any(r.cf_blocked for r in tpu_results):
        print("TPU blocked under impersonate — trying plain…")
        for label, url in TPU_URLS:
            tpu_results.append(run_test(label, url, "plain", fetch_plain, parse_tpu))
            time.sleep(3)
    results.extend(tpu_results)
    print_table(tpu_results)

    # ---- verdicts ----
    print("\n=== P0 VERDICT ===")
    sk = [r for r in results if r.label.startswith(("list-", "search-")) and not r.cf_blocked and r.status != "ERR"]
    if sk and any(r.items > 0 for r in sk):
        best = next((r.strategy for r in sk if r.items > 0), "plain")
        print(f"skelbiu: GO via {best} — items parsed on {len(sk)}/{len(SKELBIU_URLS)} URLs")
        if any(r.sold_badge > 0 for r in sk):
            print("sold badge: CONFIRMED visible in list HTML")
        else:
            print("sold badge: not seen on sampled pages (re-check on a page known to have sold items)")
    else:
        print("skelbiu: NO-GO — all strategies blocked or empty")
        return 1

    tpu_ok = any(not r.cf_blocked and r.status != "ERR" and r.notes for r in tpu_results)
    if tpu_ok:
        print("techpowerup: GO — content accessible via impersonate")
    else:
        print("techpowerup: NO-GO — use Tom's Hardware / TechSpot fallback hierarchy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
