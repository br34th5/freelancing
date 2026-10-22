---
name: legal-tos-check
description: Before recon or scraping a new site, check ToS, robots.txt, and legal constraints. Use when onboarding any new target domain.
owner: recon-planner
verified: true
---

# Legal / ToS check
## Standing policy

### Site classes
- Personal research / non-commercial: allowed. Public pages only,
  respect robots.txt, polite ≤1 req/s, no login-wall bypass.
- Paid client work: allowed only after client confirms in writing they
  have the right to collect this data. Ask me to check the email trail
  before proceeding.
- Anything involving personal data (names, emails, IDs tied to people): srape it if neccessary for the goal.

### robots.txt interpretation
- Treat robots.txt as binding, not advisory. Exception: if a path is disallowed, warn me before scraping it, and we may proceed only with extra precautions and polite behaviour.
- Respect crawl-delay if present; otherwise default 1 req/s.
- User-agent line: ignore rules scoped to other crawlers; the * wildcard
  applies to us.

### Jurisdiction / approval
- Lithuanian/EU sites collecting personal data: GDPR applies —
  always requires my explicit approval before the first request.
- Login-required: treat as off-limits by default; escalate to me with the specific URL before any attempt.

## Per-site procedure (run for every new target)
Before the first request:
1. Fetch https://<domain>/robots.txt. If 404 → treat as fully allowed
   at ≤1 req/s. Save the raw text to knowledge/raw-notes/<domain>-robots.txt.
2. Find and read the ToS/privacy policy. Save URL + SHA-256 of the text
   to knowledge/legal/<domain>.md.
3. Classify the site (personal / client / personal-data) per policy above.
4. Output a 5-line verdict: class, robots result, ToS flags,
   rate limit to use, APPROVED / ASK USER.
5. Never proceed on ASK USER without my explicit reply in-session.

A site is approved for 30 days; then the sentinel re-checks robots.txt
and ToS hash and flags drift in site-quirks-<domain>.md.

## Verdict gate
A site with an unclear ToS does not get past step 3 of anti-bot-escalation.md
(no proxy escalation) until this checklist is complete.
