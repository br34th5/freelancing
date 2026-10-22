---
name: session-debrief
description: Mandatory end-of-session debrief — record what was learned about tools, sites, and the codebase, with today's date, into the right skill or knowledge file.
owner: shared
verified: false
---

# Session debrief protocol

**Rule (AGENTS.md):** Never end a session without appending what you learned to the
relevant skill or knowledge file, dated with today's date.

## When
At the end of every session — including failed, abandoned, and escalated ones.

## Where to put learnings
| Learning type | Destination |
|---|---|
| Site behavior / quirks / block signatures | the site's knowledge file (from scraping/recon-planner/site-quirks-template.md) under /knowledge/ |
| Selector or extraction insight | scraping/scraper-builder/selector-map-protocol.md or the site's knowledge file |
| Tool/runtime behavior (verified) | owning agent's relevant skill — only if reproduced (AGENTS.md verification rule) |
| Raw, unproven observation | /knowledge/raw-notes/YYYY-MM-DD-<slug>.md, marked UNVERIFIED |
| Agent hit a trap / failed approach | the agent's known-pitfalls.md |
| The process itself broke (rules didn't help) | shared/session-debrief.md or shared/escalation-rules.md — improve the rule |

## Entry format
```
## <YYYY-MM-DD> - <agent> - <session goal in 1 line>
- Learned: <fact, concrete and testable>
- Evidence: <command run, file inspected, URL, response status>
- Verified?: yes (reproduced N times) / no (UNVERIFIED)
- Wrote to: <file path>
- Follow-up: <task or TODO left behind, or none>
```

## Quality bar
- One entry per distinct learning; no "everything went fine" entries.
- Prefer a dated line in a skill over a long essay in chat.
- If nothing was learned, still write one line saying so and why (so gaps are visible).

## Hard rule
A session is not complete until at least one debrief entry is written (even if it only
says "no new learnings").

## Log

## 2026-10-19 - (system setup) - bootstrap multi-agent skills system
- Learned: repo already had git history + user config; `.agents/` is gitignored and
  holds external Neon skills via skills-lock.json (no conflict with /skills/).
- Evidence: `git log`, `.gitignore`, `skills-lock.json` inspected.
- Verified?: yes (observed directly, 1x)
- Wrote to: this file + commit 8894af8
- Follow-up: fill TODO stubs in legal-tos-check, data-schema, selector-map-protocol,
  change-detection, alert-protocol; decide proxy conventions in anti-bot-escalation.
