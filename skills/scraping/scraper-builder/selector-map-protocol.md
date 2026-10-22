---
name: selector-map-protocol
description: How selector maps are named, versioned, stored, and updated so scrapers survive DOM changes. Use when writing or repairing extraction selectors.
owner: scraper-builder
verified: false
---

# Selector map protocol (stub)

Status: UNVERIFIED stub — storage location and format not decided.

TODO: Decide storage location (e.g. knowledge/selector-maps/<site>.yaml) and format.
TODO: Define naming convention for entries (semantic name → CSS/XPath → fallback chain).
TODO: Define versioning: when a DOM change forces a map bump, and how site-sentinel
is told to re-baseline.
TODO: Define the "verified" bar: selectors count as verified only after a live
extraction reproduces the expected fields.

## Placeholder map shape
```yaml
site: <domain>
version: 1
updated: <date>
selectors:
  <semantic_name>:
    primary: "<css or xpath>"
    fallbacks: []
    verified: false
```

## Rules
- Semantic names, not raw CSS, are the API between scraper code and maps.
- A selector that returned data once is still `verified: false` until reproduced
  in isolation (AGENTS.md verification rule).
