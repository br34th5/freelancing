# Scheduled Hacker News Scraper with Proxy Rotation

Automated scraper that runs daily via GitHub Actions or cron, with rotating proxies.

## Quick Start

```bash
# 1. Copy and fill in .env
cp .env.example ../../../../.env
# Edit .env with your proxy credentials

# 2. Run locally
python scheduled_scraper.py
```

## Proxy Setup

### Local (with .env)

Create `.env` in the **repo root** (NOT in this folder):

```env
PROXY_USER=your_proxy_username
PROXY_PASS=your_proxy_password
PROXY_LIST=host1:port1,host2:port2,host3:port3
```

**Never commit `.env`** - it's already in `.gitignore`.

### GitHub Actions (cloud)

Since the repo is public, **never put credentials in the workflow file**. Instead:

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Add these secrets:
   - `PROXY_USER` → `your_proxy_username`
   - `PROXY_PASS` → `your_proxy_password`
   - `PROXY_LIST` → comma-separated list of `host:port`

The workflow reads them automatically via `${{ secrets.PROXY_USER }}` etc.

## Scheduling

### GitHub Actions (recommended for public repos)
Runs daily at **9 AM UTC**. Manual trigger available in the Actions tab.

### Cron (local machine)
```bash
crontab -e
# Add:
0 9 * * * /path/to/venv/bin/python /path/to/scheduled_scraper.py >> /path/to/cron.log 2>&1
```

## Features

- **Proxy rotation** - Randomly picks a new proxy per request
- **Random delays** - Avoids exact timing patterns
- **Logging** - Full execution log for debugging
- **Error handling** - Graceful failures, never crashes silently
- **Date-stamped output** - Each run saves `hn_top_YYYY-MM-DD.json`

## Output

```
projects/scraping/scheduled-hn/
├── data/
│   └── hn_top_2026-10-04.json    # Daily output
├── hn_scraper.log                 # Execution log
├── scheduled_scraper.py           # Main script
├── proxy_rotator.py               # Proxy logic
├── requirements.txt
└── .github/workflows/scrape.yml   # Cloud schedule
```

## Troubleshooting

**Proxies not working?**
```bash
# Test proxies directly
python -c "
from proxy_rotator import ProxyRotator
from dotenv import load_dotenv; load_dotenv('../../../../.env')
import os
r = ProxyRotator(os.getenv('PROXY_LIST'), os.getenv('PROXY_USER'), os.getenv('PROXY_PASS'))
r.test_proxies()
"
```

**GitHub Actions fails?**
- Check Actions tab → click failed run → view logs
- Verify secrets are set in repo Settings
