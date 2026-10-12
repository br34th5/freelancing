"""Daily pipeline (P6 cron entrypoint): crawl → score → report, with alerts.

  python3 -m run_daily              # full run
  python3 -m run_daily --pages 5    # smoke crawl (then still scores + reports)

Break alerts (Discord, see alerts.py):
  - 0 items parsed
  - CF-block streak ≥ threshold (config: alert_block_streak)
  - primary DB write failure
  - scoring/report crash

Exit codes: 0 ok · 1 crawl incomplete/blocked · 2 skip (all proxies failed)
  Note: exit 2 means we tried all proxies and failed — skip silently (no alert)
"""

from __future__ import annotations

import argparse
import sys

import alerts
import spider as spider_mod

BLOCK_STREAK_ALERT = 3   # consecutive CF-blocked responses worth waking up for


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
            # All proxy attempts failed — skip silently (no alert)
            print("Crawl skipped — all proxy attempts failed")
            return 0
        if crawl["items"] == 0:
            alerts.alert_zero_items(crawl["pages"])
            return 1
        if not crawl["db_ok"]:
            alerts.alert_db_failure("see stderr / data/spider.log")
            return 1
        if crawl["max_block_streak"] >= BLOCK_STREAK_ALERT:
            # warn but keep going — retries may have recovered the crawl
            alerts.alert_block_streak(crawl["max_block_streak"])
        if not crawl["completed"]:
            # paused (SIGINT / budget) is fine; incomplete without pause is not
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
