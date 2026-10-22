---
name: change-detection
description: How site-sentinel watches a live site for DOM, content, and schema changes — baselines, diffs, and noise filters. Use when monitoring a scraped site over time.
owner: site-sentinel
verified: false
---

# Change detection (stub)

Status: UNVERIFIED stub — method and cadence not decided.

TODO: Define baseline capture method (full snapshot vs. field hash vs. screenshot).
TODO: Define diff granularity (DOM tree hash, text content, selector presence,
API payload shape).
TODO: Define noise filters (timestamps, ad slots, session tokens, A/B variants).
TODO: Define check cadence per site and cost limits.
TODO: Point to existing monitoring scripts if any (link project paths).

## Rules
- A baseline is only trusted if the selectors it hashes were verified by a live
  extraction (verification rule).
- When a change is confirmed, route to alert-protocol.md and update the site's
  knowledge file change log.
