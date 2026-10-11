"""Discord break alerts (P6). Reuses the scheduled-hn send_alert pattern.

Env (checked in order): DISCORD_WEBHOOK_URL, ALERT_WEBHOOK_URL (repo-root .env
or price-tracker/.env — database.py already loads both).

Break conditions the daily pipeline alerts on:
  - crawl produced 0 items (site changed / total block)
  - CF-block streak (consecutive blocked responses ≥ threshold)
  - primary DB write failure
  - pipeline exception (spider/scoring/report crash)
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

# same env resolution as database.py: repo-root .env, then local override
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path(__file__).parent / ".env", override=True)

WEBHOOK_ENVS = ("DISCORD_WEBHOOK_URL", "ALERT_WEBHOOK_URL")
BOT_NAME = "skelbiu-price-tracker"


def send_alert(message: str, webhook_url: str | None = None) -> bool:
    """Post a message to Discord. Returns True on success; never raises."""
    if webhook_url is None:
        for env in WEBHOOK_ENVS:
            webhook_url = os.getenv(env)
            if webhook_url:
                break
    if not webhook_url:
        print(f"[alerts] no webhook configured, skipping: {message}")
        return False
    try:
        resp = requests.post(
            webhook_url,
            json={"content": message[:1900], "username": BOT_NAME},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[alerts] failed to send: {e}")
        return False


def alert_zero_items(pages: int) -> bool:
    return send_alert(
        f"🔴 skelbiu crawl produced 0 items ({pages} pages fetched) — "
        "site layout change or total CF block? Check data/spider.log"
    )


def alert_block_streak(streak: int) -> bool:
    return send_alert(
        f"🟠 skelbiu CF-block streak: {streak} consecutive blocked responses — "
        "check proxies / cool down. data/spider.log"
    )


def alert_db_failure(error: str) -> bool:
    return send_alert(
        f"🔴 skelbiu DB write failed (primary): {error[:200]}"
    )


def alert_pipeline_error(step: str, error: str) -> bool:
    return send_alert(
        f"🔴 skelbiu pipeline crash in {step}: {error[:200]}"
    )


def send_test() -> bool:
    return send_alert("✅ test alert from skelbiu price-tracker — alerts work!")
