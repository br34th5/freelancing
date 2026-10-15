"""Cloudflare Turnstile solver with CapSolver fallback.

When scrapling's built-in auto-solver fails on "managed" turnstile challenges,
this module provides a CapSolver API fallback. CapSolver is known to handle
managed turnstile where browser-based auto-solve fails.

Usage:
    solver = CaptchaSolver(api_key="CAP_xxx")  # or CAPSOLVER_API_KEY env
    token = solver.solve_turnstile(site_key, site_url)

Env:
    CAPSOLVER_API_KEY   — CapSolver API key (free tier: 100 solves/day)
    CAPTCHA_TIMEOUT     — solve timeout in seconds (default: 120)
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    _env_paths = [
        Path(__file__).resolve().parents[3] / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for p in _env_paths:
        if p.exists():
            load_dotenv(p)
except ImportError:
    pass


CAPSOLVER_API_URL = "https://api.capsolver.com"
DEFAULT_TIMEOUT = 120
POLL_INTERVAL = 3


class CaptchaSolver:
    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key or os.getenv("CAPSOLVER_API_KEY", "")
        self.timeout = int(os.getenv("CAPTCHA_TIMEOUT", str(timeout)))

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def solve_turnstile(self, site_key: str, site_url: str) -> Optional[str]:
        if not self.available:
            return None

        task_id = self._create_turnstile_task(site_key, site_url)
        if not task_id:
            return None

        return self._poll_result(task_id)

    def _create_turnstile_task(self, site_key: str, site_url: str) -> Optional[str]:
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": site_url,
                "websiteKey": site_key,
            }
        }
        try:
            resp = requests.post(
                f"{CAPSOLVER_API_URL}/createTask",
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errorId") == 0:
                return data.get("taskId")
            print(f"[captcha] CapSolver createTask error: {data.get('errorDescription')}")
        except Exception as e:
            print(f"[captcha] CapSolver createTask failed: {e}")
        return None

    def _poll_result(self, task_id: str) -> Optional[str]:
        deadline = time.time() + self.timeout
        payload = {
            "clientKey": self.api_key,
            "taskId": task_id,
        }
        while time.time() < deadline:
            try:
                resp = requests.post(
                    f"{CAPSOLVER_API_URL}/getTaskResult",
                    json=payload,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status", "")
                if status == "ready":
                    solution = data.get("solution", {})
                    return solution.get("token") or solution.get("turnToken")
                if status == "failed" or data.get("errorId", 0) != 0:
                    print(f"[captcha] CapSolver task failed: {data.get('errorDescription')}")
                    return None
            except Exception as e:
                print(f"[captcha] CapSolver poll error: {e}")
            time.sleep(POLL_INTERVAL)

        print(f"[captcha] CapSolver timed out after {self.timeout}s")
        return None


class CaptchaStateTracker:
    """Track CF challenge state across runs to inform scheduling decisions."""

    def __init__(self, state_file: str | Path = "data/cf_state.json"):
        self.state_file = Path(state_file)
        self._state = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            try:
                import json
                return json.loads(self.state_file.read_text())
            except Exception:
                pass
        return {"history": [], "last_success_utc": None, "last_failure_utc": None,
                "consecutive_failures": 0, "best_hour_utc": None}

    def record_success(self, hour_utc: int) -> None:
        import json
        self._state["last_success_utc"] = hour_utc
        self._state["consecutive_failures"] = 0
        self._state["history"].append({"hour": hour_utc, "ok": True})
        self._state["history"] = self._state["history"][-30:]
        self._save()

    def record_failure(self, hour_utc: int) -> None:
        import json
        self._state["last_failure_utc"] = hour_utc
        self._state["consecutive_failures"] += 1
        self._state["history"].append({"hour": hour_utc, "ok": False})
        self._state["history"] = self._state["history"][-30:]
        best = self._best_hours()
        if best:
            self._state["best_hour_utc"] = best[0]
        self._save()

    def _save(self) -> None:
        import json
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self._state, indent=2))

    def _best_hours(self) -> list[tuple[int, int]]:
        from collections import Counter
        success_hours = [h["hour"] for h in self._state["history"] if h["ok"]]
        if not success_hours:
            return []
        return Counter(success_hours).most_common(3)

    def suggest_run_hours(self) -> list[int]:
        best = self._best_hours()
        if best:
            return [h for h, _ in best]
        return [6, 12, 18]
