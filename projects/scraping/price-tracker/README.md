# skelbiu.lt Electronics Price Tracker

Daily polite scrape of skelbiu.lt (laptops, desktops, gaming PCs, servers,
CPUs, GPUs) → price history (asking + sold/reserved) → performance scoring
(GPU resolution brackets, CPU genre tiers) → projected buy&sell profit report.

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

# 5. full daily run
python3 -m run_daily
```

## Scheduling (GitHub Actions)

Runs daily via `.github/workflows/price-tracker.yml` at **06:10 UTC**
(≈09:10 Europe/Vilnius in summer) + `workflow_dispatch` for manual runs.
No local cron — the dev machine doesn't need to be on.

**Required repo secrets:**
- `DATABASE_URL` — Neon connection string (becomes the primary when no
  local DB is configured; dual-write is local-only)
- `ALERT_WEBHOOK_URL` — Discord webhook (already set, shared with HN scraper)
- `PROXY_LIST` or `PROXY_HOSTS`+`PROXY_USER`+`PROXY_PASS` — **recommended**:
  GitHub's datacenter IP may trip skelbiu's Cloudflare; the residential
  proxy pool avoids this. Without proxies, watch the first scheduled runs
  for 0-item alerts.

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

- `subcats` / `watched_slugs` — crawl scope · `max_pages_per_run` kill-switch
- `fees_pct` (10) · `refurb_eur` per tier · `min_profit_eur` (50)
- `vram_gates` · `genre_weights` · `g3d_bands` · `tier_thresholds`
- `ad_keyword_guard` · `junk_patterns` — listing quality filters

## Tests

```bash
python3 test_extract.py    # list-page parsing (offline)
python3 test_specs.py      # title → CPU/GPU extraction
python3 test_database.py   # loaders + transitions (needs local Postgres)
python3 test_scoring.py    # brackets, tiers, resolution (offline)
python3 test_report.py     # profit engine + report rendering (offline)
```

## Politeness budget

1×/day · ~70 pages × 3–5s delay ≈ 4–5 min · serial concurrency ·
AutoThrottle · robots path-URLs only · TLS impersonation (no browser) ·
`?type=1` (Siūlo only) · alert-only-on-failure · `max_pages_per_run` kill-switch.

## Reference data

Bi-monthly (60d): `python3 -m benchmarks refresh` (PassMark CPU/GPU + TPU
recommended-resolution cache). Missing models are reported in the report's
unscored section — refresh manually with
`python3 -m benchmarks --refresh <model>`.
