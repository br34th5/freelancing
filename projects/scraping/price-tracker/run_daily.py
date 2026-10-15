"""Daily pipeline (P6 cron entrypoint): crawl → score → report, with alerts.

  python3 -m run_daily              # full run
  python3 -m run_daily --pages 5    # smoke crawl (then still scores + reports)

Break alerts (Discord, see alerts.py):
  - 0 items parsed
  - CF-block streak ≥ threshold (config: alert_block_streak)
  - primary DB write failure
  - scoring/report crash

Captcha fallback:
  If stealthy engine returns 0 items, tries CapSolver API (if CAPSOLVER_API_KEY
  is set) as a secondary fetch method. Tracks CF state in data/cf_state.json
  to inform which hours work best.

Exit codes: 0 ok · 1 crawl incomplete/blocked · 2 skip (all proxies failed)
  Note: exit 2 means we tried all proxies and failed — skip silently (no alert)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import alerts
import spider as spider_mod
from captcha_solver import CaptchaStateTracker

BLOCK_STREAK_ALERT = 3

_cf_state = CaptchaStateTracker(Path(__file__).parent / "data" / "cf_state.json")


def _record_cf_result(success: bool) -> None:
    hour = datetime.now(timezone.utc).hour
    if success:
        _cf_state.record_success(hour)
    else:
        _cf_state.record_failure(hour)


def _try_capsolver_fallback(max_pages: int | None = None) -> list[dict]:
    """Attempt to fetch via CapSolver tokens when stealthy engine fails."""
    try:
        from captcha_solver import CaptchaSolver
    except ImportError:
        print("CapSolver fallback: captcha_solver module not available")
        return []

    solver = CaptchaSolver()
    if not solver.available:
        print("CapSolver fallback: no API key configured (CAPSOLVER_API_KEY)")
        return []

    print("CapSolver fallback: attempting fetch with CapSolver API...")

    config = json.loads((Path(__file__).parent / "config.json").read_text())
    pages_limit = max_pages or config.get("pages_per_subcat", 5)
    all_items: list[dict] = []
    site_url = "https://www.skelbiu.lt"
    site_key = "0x4AAAAAABk"
    type_q = f"?type={config['type_filter']}" if config.get("type_filter") else ""

    import requests as req

    for cat, base_url in config["subcats"].items():
        seed = f"{base_url.rstrip('/')}/{type_q}"
        page_url = seed
        page_no = 1

        while page_url and page_no <= pages_limit:
            print(f"  fetching {cat} page {page_no}: {page_url}")

            token = solver.solve_turnstile(site_key=site_key, site_url=site_url)
            if not token:
                print(f"  CapSolver failed for {cat} page {page_no}")
                break

            try:
                headers = {
                    "cf-turnstile-response": token,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": site_url,
                }
                resp = req.get(page_url, headers=headers, timeout=30)
                if resp.status_code != 200:
                    print(f"  HTTP {resp.status_code}")
                    break
            except Exception as e:
                print(f"  fetch error: {e}")
                break

            from extract import parse_list_page
            listings, next_url = parse_list_page(resp.text, page_url, cat)
            for l in listings:
                all_items.append({
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
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "source_page": page_url,
                })
            print(f"  got {len(listings)} listings")

            page_url = next_url
            page_no += 1

    return all_items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=None,
                    help="override max_pages_per_run (smoke test)")
    ap.add_argument("--skip-crawl", action="store_true",
                    help="score + report only (no fetch)")
    args = ap.parse_args()

    # 1) crawl -----------------------------------------------------------
    if not args.skip_crawl:
        try:
            crawl = spider_mod.run(max_pages=args.pages)
        except Exception as e:
            alerts.alert_pipeline_error("spider", repr(e))
            return 2
        if crawl.get("exit_code") == 2:
            print("Crawl skipped — all proxy attempts failed")
            return 0
        if crawl["items"] == 0:
            _record_cf_result(False)

            fallback_items = _try_capsolver_fallback(max_pages=args.pages)
            if fallback_items:
                print(f"CapSolver fallback recovered {len(fallback_items)} items!")
                obs_path = Path(__file__).parent / "data" / "observations.jsonl"
                with obs_path.open("a") as f:
                    for it in fallback_items:
                        f.write(json.dumps(it, ensure_ascii=False) + "\n")
                try:
                    from database import save_items
                    stats = save_items(fallback_items)
                    for role, s in stats.items():
                        print(f"  {role}: {s.summary()}")
                    crawl["db_ok"] = True
                except Exception as e:
                    print(f"DB write failed (fallback): {e}", file=sys.stderr)
                    crawl["db_ok"] = False

                crawl["items"] = len(fallback_items)
                _record_cf_result(True)
            else:
                alerts.alert_zero_items(crawl["pages"])
                return 1
        else:
            _record_cf_result(True)

        if crawl.get("db_ok") is False:
            alerts.alert_db_failure("see stderr / data/spider.log")
            return 1
        if crawl.get("max_block_streak", 0) >= BLOCK_STREAK_ALERT:
            alerts.alert_block_streak(crawl["max_block_streak"])
        if not crawl["completed"]:
            if not crawl["paused"]:
                alerts.send_alert(
                    f"🟠 skelbiu crawl incomplete (pages={crawl['pages']}, "
                    f"items={crawl['items']}) — check data/spider.log"
                )
                return 1

    # 2) score ------------------------------------------------------------
    try:
        import scoring
        scoring.run_score()
        print("scoring done")
    except Exception as e:
        alerts.alert_pipeline_error("scoring", repr(e))
        return 2

    # 3) report -----------------------------------------------------------
    try:
        import report
        report.run()
    except Exception as e:
        alerts.alert_pipeline_error("report", repr(e))
        return 2

    print("\npipeline complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
