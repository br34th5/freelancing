# skelbiu.lt Electronics Price Tracker

Every-2-days polite scrape of skelbiu.lt (desktops, gaming PCs, servers,
CPUs, GPUs) → price history (asking + sold/reserved) → performance scoring
(GPU resolution brackets, CPU genre tiers) → projected buy&sell profit report.

**Laptops is excluded from the daily run** — that category trips aggressive
Cloudflare protection that quickly burns the Oxylabs quota. Refresh
laptops manually with `python -m scrape_laptops` from your own IP.

## Pipeline

```
spider.py      Scrapling spider (TLS impersonation, robots-obeying, serial,
               AutoThrottle, pause/resume) → database (local Postgres primary
               + Neon dual-write) + data/observations.jsonl backup
scoring.py     GPU brackets (TPU reco → VRAM gate → G3D-band fallback),
               CPU genre tiers, whole-PC min(bracket, genre) + bottleneck
               → listing_specs
report.py      comparables (same model OR ±10% combined bucket, 90d) →
               profit = est_resale×(1−fees) − asking − refurb[tier]
               → data/report.txt + data/report.html (chart.js)
run_daily.py   cron entrypoint: crawl → score → report, Discord alerts on
               break conditions (see below)
```

## Quick start

```bash
# 1. env (repo root .env is the primary source; local .env overrides)
cp .env.example .env        # fill LOCAL_DATABASE_URL, proxies, webhook

# 2. init schema
python3 -m database --init

# 3. refresh benchmark reference data (60d cadence; PassMark + TPU reco)
python3 -m benchmarks refresh

# 4. smoke crawl + score + report
python3 -m run_daily --pages 3

# 5. full scheduled run (same as the Actions cron)
python3 -m run_daily
```

## Scheduling (GitHub Actions)

Runs every 2 days via `.github/workflows/price-tracker.yml` at **00:00 UTC**
(=03:00 Europe/Vilnius in summer EEST, ≈02:00 in winter EET — a single UTC
cron can't follow DST) + `workflow_dispatch` for manual runs. No local cron —
the dev machine doesn't need to be on.

Every-2-days cadence: Oxylabs is $5/month and laptops is excluded (see
below), so two days is enough cadence for sold-state transitions to
accumulate without burning quota on idle scrapes
(~15 runs/month × ~$0.03 ≈ $0.45/month).

**Manual laptops scrape:**

```bash
python -m scrape_laptops            # first 3 pages (~10 min, no proxy)
python -m scrape_laptops --pages 5  # deeper crawl
```

Runs the same parse + detail-enrichment logic as the main spider, but
uses the fetcher engine (TLS impersonation) without a proxy, against
your own IP. CF may still block occasionally — the script exits cleanly
in that case, no alerts.

**Required repo secrets:**
- `DATABASE_URL` — Neon connection string (becomes the primary when no
  local DB is configured; dual-write is local-only)
- `ALERT_WEBHOOK_URL` — Discord webhook (already set, shared with HN scraper)
- `PROXY_SERVER` — Oxylabs proxy server URL (e.g. `http://pr.oxylabs.io:7777`)
- `PROXY_USER` — Oxylabs username (e.g. `customer-resi_XXXXX-sessid-...`)
- `PROXY_PASS` — Oxylabs password
- `FALLBACK_PROXY_LIST` — LT datacenter proxies (comma-separated, used when Oxylabs fails)
- `FALLBACK_PROXY_USER` — Fallback proxy username
- `FALLBACK_PROXY_PASS` — Fallback proxy password

**Proxy fallback logic:**
- **Primary:** Oxylabs residential (StealthyFetcher + CF auto-solve) — most robust, ~$0.03/run
- **Fallback:** LT datacenter proxies (Fetcher, likely blocked but free) — last resort when Oxylabs quota exhausted
- **If both fail:** Skip run silently (exit 0, no alert) — proxy limit exceeded or CF too aggressive

Oxylabs has $5/month quota. When exhausted, proxy returns connection errors. LT proxies are free but on CF's blocklist — they're a last-ditch fallback. Without any working proxy, the run is skipped.

Each run uploads `report.txt` / `report.html` / logs as a 14-day artifact.
For a local full run: `python3 -m run_daily`. Logs: `data/spider.log`
(crawl detail).

## Alerts (Discord)

Set `DISCORD_WEBHOOK_URL` (or `ALERT_WEBHOOK_URL`) in `.env`. Fires on:

| Condition | Severity |
|---|---|
| 0 items parsed (layout change / total block) | 🔴 exit 1 |
| CF-block streak ≥ 3 (warns, run continues) | 🟠 |
| Primary DB write failure | 🔴 exit 1 |
| Scoring/report crash | 🔴 exit 2 |

Test: `python3 -c "import alerts; alerts.send_test()"`

## Config (config.json)

- `subcats` / `manual_laptops_url` — crawl scope (laptops is manual-only,
  see Scheduling). `watched_slugs` — extra search keywords.
- `max_pages_per_run` kill-switch
- `fees_pct` (10) · `refurb_eur` per tier · `min_profit_eur` (50)
- `fetch_engine`: `stealthy` (real Chrome + CF auto-solve) or `fetcher` (TLS impersonation, faster)
- `vram_gates` · `genre_weights` · `g3d_bands` · `tier_thresholds`
- `ad_keyword_guard` · `junk_patterns` — listing quality filters

## Crawl engines

| Engine | Speed | CF bypass | Proxy support | Use when |
|---|---|---|---|---|
| `stealthy` | ~17s/page | ✅ auto-solve | ✅ dict format | Actions, aggressive CF sites |
| `fetcher` | ~0.5s/page |  TLS only | ✅ URL list format | Local dev, permissive sites |

Override via env: `FETCH_ENGINE=fetcher python3 -m spider`

## Tests

```bash
python3 test_extract.py    # list-page parsing (offline)
python3 test_specs.py      # title → CPU/GPU extraction
python3 test_database.py   # loaders + transitions (needs local Postgres)
python3 test_scoring.py    # brackets, tiers, resolution (offline)
python3 test_report.py     # profit engine + report rendering (offline)
```

## Politeness budget

Every 2 days · ~25 pages × 3–5s delay ≈ 7–10 min · serial concurrency ·
AutoThrottle · robots path-URLs only · TLS impersonation (no browser for
the manual laptops script, real Chrome for the daily stealthy run) ·
`?type=1` (Siūlo only) · alert-only-on-failure · `max_pages_per_run`
kill-switch. Laptops scrape (manual) uses 1.5× wider delays since it
hits skelbiu from a single residential IP.

## Reference data

Bi-monthly (60d): `python3 -m benchmarks refresh` (PassMark CPU/GPU + TPU
recommended-resolution cache). Missing models are reported in the report's
unscored section — refresh manually with
`python3 -m benchmarks --refresh <model>`.
