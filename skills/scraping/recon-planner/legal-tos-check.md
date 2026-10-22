---
name: legal-tos-check
description: Before recon or scraping a new site, check ToS, robots.txt, and legal constraints. Use when onboarding any new target domain.
owner: recon-planner
verified: false
---

# Legal / ToS check (stub)

Status: UNVERIFIED stub — no standing policy decided yet.

TODO: Define my standing policy for site classes I scrape (personal research /
paid client / public data only).
TODO: Record robots.txt interpretation rules I want followed (crawl-delay,
disallow paths, which agents).
TODO: Note jurisdictions/clients where explicit written approval is required.
TODO: Fill the checklist below for each new site; archive the ToS text hash.

## Minimum checklist (fill per site)
- [ ] robots.txt read and archived — date:
- [ ] ToS / Terms URL + relevant clauses summarized — date:
- [ ] Personal data in scope? (names, emails, phones) → retention + purpose recorded
- [ ] Commercial redistribution allowed?
- [ ] Explicit approval on file (link) if required:

## Verdict gate
A site with an unclear ToS does not get past step 3 of anti-bot-escalation.md
(no proxy escalation) until this checklist is complete.
