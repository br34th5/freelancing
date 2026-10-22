---
name: data-schema
description: Defines the target data shape for a scrape — fields, types, validation, output format. Use when starting or extending scraper-builder work for a site.
owner: scraper-builder
verified: false
---

# Data schema

## Storage hierarchy (all sites)

1. **PRIMARY: Neon Postgres** (cloud). Every scrape writes here first.
   One database per domain, one table per entity type.
   Connection string from env var NEON_DATABASE_URL — never hardcoded.
2. **BACKUP: local Postgres** (may be offline — treat as
   best-effort). After a successful Neon write, attempt a local mirror
   insert. If local is unreachable: skip silently, log to run report,
   and continue. Never fail a run because the backup is down.
3. **EXPORT: JSONL** (one file per run, newline-delimited JSON, one
   object per record). Written alongside the DB write to
   /data/exports/<domain>/<YYYY-MM-DD>-<run-id>.jsonl — for eyeballing,
   diffing between runs, and recovering a run without DB access.

Write order per batch: JSONL → Neon → local mirror. JSONL first means
a crashed run never loses data already extracted.

## Standard field conventions (every table/file includes)

- id            : uuid, primary key, generated at insert
- scraped_at    : timestamptz, UTC, NOT NULL — when the row was collected
- source_url    : text, NOT NULL — the exact page/API URL it came from
- run_id        : text, NOT NULL — groups rows from one scrape session
- schema_version: int, default 1 — bump when fields change
- raw_html_ref  : text, nullable — path to archived raw HTML snapshot
                  in /data/raw/<domain>/... (archival optional per site,
                  see site quirks; storage is cheap, re-crawling isn't)

Entity fields come after these, alphabetical.

## Validation rule

A row that fails field validation (regex/range/enum) is REJECTED:
never written to Neon, logged to the run report with the reason, and
counted in the run summary. Never silently coerce or drop fields.
Fail loudly at the end: "3 rows rejected: reasons".

## Schema versioning

When a site changes and fields change: new schema_version in the
site's quirks file, ALTER TABLE / additive columns in Neon
(never destructive — old data must stay readable), and a note in
site-quirks-<domain>.md describing what changed.

## Schema template (per site, saved as knowledge/schemas/<domain>.yaml)
```yaml
site: example.com
version: 1
fields:
  - name: title
    type: string
    required: true
    source: selector: .product-title
    validation: min_length 1
  - name: price
    type: float
    required: true
    source: selector: [data-testid=price]
    validation: ">= 0"
  - name: in_stock
    type: bool
    required: true
    source: selector: .availability-badge
    validation: enum [true, false]
output:
  database: neon://<db-name>
  table: products
  jsonl: /data/exports/example.com/
```

## Rules
- Every scraped field has a named source that maps into the site's selector map
  (see selector-map-protocol.md).
- Bump the schema version on any breaking field change; note it in the site's
  knowledge file.
- A field is not "done" until one live extraction reproduces it (verification rule).
