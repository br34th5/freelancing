# Skills catalog

Read this first, every task. Pick one agent. Load only its skills + /skills/shared/. See /AGENTS.md.

**Also read /knowledge/TODO.md at session start — it lists open work (unfilled stubs, disabled scraper).**

## Agents (specialization domains)
- **recon-planner** — domain: scraping. Site recon, anti-bot strategy, legal/TOS gates, site-quirk capture.
- **scraper-builder** — domain: scraping. Builds and repairs scrapers: data schemas, selector maps, extraction code.
- **site-sentinel** — domain: scraping. Monitors live sites for DOM/content drift, baselines, and alerts.

## Shared (every agent may read)
- escalation-rules | shared | 3-fail / out-of-scope STOP rule + escalation summary format.
- session-debrief | shared | Mandatory end-of-session learning capture protocol.

## scraping / recon-planner
- anti-bot-escalation | recon-planner | 403→slow→rotate UA→proxy→stop ladder for blocked targets.
- legal-tos-check | recon-planner | Pre-scrape ToS/robots/legal gate. Stub — policy undecided.
- site-quirks-template | recon-planner | Blank per-site quirks template for /knowledge/ files.
- known-pitfalls | recon-planner | Rolling log of failed attempts. Stub.

## scraping / scraper-builder
- data-schema | scraper-builder | Target data shape for a scrape. Stub.
- selector-map-protocol | scraper-builder | Naming, versioning, verification of selectors. Stub.
- session-debrief | scraper-builder | Debrief wrapper → shared/session-debrief.md.
- known-pitfalls | scraper-builder | Rolling log of failed attempts. Stub.

## scraping / site-sentinel
- change-detection | site-sentinel | Baselines and diffs for live-site monitoring. Stub.
- alert-protocol | site-sentinel | Severity levels and notification channels on drift. Stub.
- known-pitfalls | site-sentinel | Rolling log of failed attempts. Stub.
