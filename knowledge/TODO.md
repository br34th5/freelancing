# TODO — next session

Last updated: 2026-10-22 (read this at session start, alongside /skills/_index.md).

## Done today (2026-10-22)
- anti-bot-escalation.md: proxy infra filled in (Webshare pool + Oxylabs quota rules) — verified: true
- legal-tos-check.md: standing policy, robots rules, per-site procedure — verified: true
- data-schema.md: Neon-primary storage hierarchy, field conventions, validation — verified: false (needs one live run to reproduce before flipping)
- price-tracker GitHub Actions schedule disabled (workflow_dispatch only)

## Left to do
1. **skill stubs still unfilled** (all TODO markers remain):
   - skills/scraping/scraper-builder/selector-map-protocol.md — storage location, naming, versioning, verified bar
   - skills/scraping/site-sentinel/change-detection.md — baseline method, diff granularity, noise filters, cadence, script links
   - skills/scraping/site-sentinel/alert-protocol.md — severity levels, channels, auto-response, follow-up owners
2. **price-tracker scraper adjustments** → then re-enable cron in
   .github/workflows/price-tracker.yml (comment block marks the spot).
   Root cause of failures still to diagnose: check recent red runs in
   GitHub Actions + data/spider.log.
3. **data-schema.md verification**: reproduce one end-to-end write
   (JSONL → Neon → mirror) in isolation, then flip `verified: true`.
4. **dirs referenced by new skills but not yet created**:
   knowledge/proxy/oxylabs-quota.md, knowledge/legal/<domain>.md,
   knowledge/schemas/<domain>.yaml, knowledge/selector-maps/.

## Debrief pointer
Session log: skills/shared/session-debrief.md → "Log" section.
