---
name: site-quirks-template
description: Blank template for recording per-site quirks — anti-bot behavior, DOM structure, rate limits, auth. Copy into a new file under /knowledge/ when onboarding a site.
owner: recon-planner
verified: false
---

# Site quirks: <site-name>

- Domain:
- First recon date:
- Last verified: (leave blank until reproduced)

## Access
- Auth required:
- robots.txt summary:
- ToS status: (link to legal-tos-check result)

## Anti-bot
- CDN / WAF:
- Known block signatures:
- Rate limit observed:
- Ladder position reached (see anti-bot-escalation.md):

## Structure
- Tech stack guess (SSR / SPA / framework):
- Pagination pattern:
- JS-required regions:
- Session / cookie requirements:

## Selectors (hand off to scraper-builder)
- List page:
- Detail page:
- Price / target field:
- Fragile selectors to watch:

## Notes

## Change log
- <date>: created (UNVERIFIED)
