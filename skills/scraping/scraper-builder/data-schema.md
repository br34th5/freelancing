---
name: data-schema
description: Defines the target data shape for a scrape — fields, types, validation, output format. Use when starting or extending scraper-builder work for a site.
owner: scraper-builder
verified: false
---

# Data schema (stub)

Status: UNVERIFIED stub — conventions not finalized.

TODO: Define my standard output formats (JSONL / CSV / SQLite) and where files live.
TODO: Define default field conventions (scraped_at, source_url, raw_html_ref).
TODO: Add one completed example schema for an existing project.

## Schema template
```yaml
site: <domain>
version: <schema version>
fields:
  - name: <field>
    type: string | int | float | bool | datetime | url
    required: true | false
    source: <selector name or api field>
    validation: <regex / range / enum>
output: <jsonl | csv | sqlite path>
```

## Rules
- Every scraped field has a named source that maps into the site's selector map
  (see selector-map-protocol.md).
- Bump the schema version on any breaking field change; note it in the site's
  knowledge file.
- A field is not "done" until one live extraction reproduces it (verification rule).
