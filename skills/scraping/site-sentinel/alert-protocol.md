---
name: alert-protocol
description: What site-sentinel does when it detects a change — severity levels, notification channels, and who acts. Use when a monitored site drifts from its baseline.
owner: site-sentinel
verified: false
---

# Alert protocol (stub)

Status: UNVERIFIED stub — channels and owners not decided.

TODO: Define severity levels (cosmetic / structural / functional breakage).
TODO: Define notification channels (log file, email, Slack/Discord, task queue)
and thresholds per channel.
TODO: Define auto-response rules (e.g. pause scraper after 3 consecutive failures)
vs. human-only responses.
TODO: Define follow-up ownership (recon-planner re-recon? scraper-builder patch?).

## Rules
- Every alert includes: site, baseline version, change summary, severity,
  and the evidence path (diff/screenshot).
- Never auto-patch selectors from a sentinel alert without a debrief entry
  (see /skills/shared/session-debrief.md).
