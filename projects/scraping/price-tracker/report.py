"""P5 — comparables → deals list → report.txt + report.html.

Profit engine (from plan):
    est_resale = median(sold comparables: same model OR ±10% combined-score
                        bucket, last 90d)
    profit     = est_resale × (1 − fees_pct/100) − asking − refurb[tier]
    confidence = n≥3 high · 1–2 med · 0 low (fallback: tier median)
A listing counts as a DEAL only when profit ≥ min_profit_eur AND
confidence ≥ med.

Unscored-models warning: active listings whose spec_confidence is not 'low'
but which resolved to no benchmark score (cpu_score and gpu_score both NULL).
Report surfaces count + titles + a `benchmarks.py --refresh <model>` hint.

Usage:
    python3 -m report                # write report.txt + report.html
    python3 -m report --min-days 14  # widen comparables window (default 90)
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import _primary_conn, SCHEMA

DATA = Path(__file__).parent / "data"
CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())

DEAL_WINDOW_DAYS = 90


# ------------------------------------------------------------------- helpers

def _med(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _conf(n: int) -> str:
    if n >= 3:
        return "high"
    if n >= 2:
        return "med"
    return "low"


def _fee_factor() -> float:
    return 1.0 - CONFIG.get("fees_pct", 10) / 100.0


def _refurb(tier: str | None) -> float:
    return float(CONFIG.get("refurb_eur", {}).get(tier or "not-capable", 0))


# ------------------------------------------------------------------- queries

def fetch_deal_inputs(conn) -> list[dict]:
    """Active, not-junk, spec_confidence ≥ med listings with a score to sell."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT l.item_id, l.title, l.url, l.category, l.location,
                   s.device_class, s.canonical_model, s.combined_score, s.tier,
                   s.bottleneck, s.spec_confidence,
                   (SELECT o.price_eur FROM {SCHEMA}.observations o
                    WHERE o.item_id = l.item_id
                    ORDER BY o.observed_at DESC LIMIT 1) AS asking_eur
            FROM {SCHEMA}.listings l
            JOIN {SCHEMA}.listing_specs s ON s.item_id = l.item_id
            WHERE l.last_state = 'active'
              AND s.spec_confidence <> 'low'
              AND (s.cpu_score IS NOT NULL OR s.gpu_score IS NOT NULL)
              AND (SELECT o.price_eur FROM {SCHEMA}.observations o
                   WHERE o.item_id = l.item_id
                   ORDER BY o.observed_at DESC LIMIT 1) IS NOT NULL
            ORDER BY s.combined_score DESC NULLS LAST
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_sold_pool(conn, window_days: int) -> list[dict]:
    """Sold listings with the price they sold at, last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT DISTINCT ON (t.item_id)
                   t.item_id, l.title, l.category, t.price_eur, t.detected_at,
                   s.device_class, s.canonical_model, s.combined_score, s.tier
            FROM {SCHEMA}.state_transitions t
            JOIN {SCHEMA}.listings l ON l.item_id = t.item_id
            LEFT JOIN {SCHEMA}.listing_specs s ON s.item_id = t.item_id
            WHERE t.to_state = 'sold'
              AND t.price_eur IS NOT NULL
              AND t.detected_at >= %s
            ORDER BY t.item_id, t.detected_at DESC
        """, (cutoff,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_unscored(conn) -> list[dict]:
    """Active listings that should have scored but didn't (benchmark gap)."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT l.item_id, l.title, l.category,
                   s.device_class, s.canonical_model, s.cpu_model, s.gpu_model,
                   s.spec_confidence
            FROM {SCHEMA}.listings l
            JOIN {SCHEMA}.listing_specs s ON s.item_id = l.item_id
            WHERE l.last_state = 'active'
              AND s.spec_confidence <> 'low'
              AND s.cpu_score IS NULL AND s.gpu_score IS NULL
            ORDER BY l.title
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# --------------------------------------------------------------- comparables

def _bucket(a: float | None, b: float | None) -> bool:
    """True when two combined scores sit within ±10% of each other."""
    if a is None or b is None:
        return False
    hi, lo = max(a, b), min(a, b)
    return lo > 0 and (hi - lo) / lo <= 0.10


def find_comparables(deal: dict, pool: list[dict]) -> list[dict]:
    """Same canonical model (exact) OR same device_class + ±10% combined-score bucket.

    device_class must match: a laptop and a desktop with the same score are NOT
    comparable (different resale markets).  Without this filter, every mid-tier
    listing clustered around the single mid-tier sale price.
    """
    model = (deal.get("canonical_model") or "").strip()
    deal_class = (deal.get("device_class") or "").strip()
    out = []
    for sold in pool:
        sold_class = (sold.get("device_class") or "").strip()
        # same model → always a comp (even across device_class, e.g. eGPU setup)
        if model and (sold.get("canonical_model") or "").strip() == model:
            out.append(sold)
        # score-bucket match → require same device_class
        elif deal_class and sold_class == deal_class:
            if _bucket(deal.get("combined_score"), sold.get("combined_score")):
                out.append(sold)
    return out


def tier_fallback_median(deal: dict, pool: list[dict]) -> float | None:
    """No model/bucket comps → median of same-tier + same-device_class sold listings.

    Also requires combined_score within ±30% to avoid matching a €250 laptop
    to a €3,400 laptop just because they share a tier label.
    """
    tier = deal.get("tier")
    if not tier:
        return None
    deal_class = (deal.get("device_class") or "").strip()
    deal_score = deal.get("combined_score")
    prices = []
    for s in pool:
        if s.get("tier") != tier:
            continue
        if deal_class and (s.get("device_class") or "").strip() != deal_class:
            continue
        # score-similarity gate (wider than comparables' ±10%)
        s_score = s.get("combined_score")
        if deal_score is not None and s_score is not None:
            hi, lo = max(deal_score, s_score), min(deal_score, s_score)
            if lo > 0 and (hi - lo) / lo > 0.30:
                continue
        if s.get("price_eur") is not None:
            prices.append(float(s["price_eur"]))
    return _med(prices)


# ------------------------------------------------------------------ profit

def score_deal(deal: dict, pool: list[dict]) -> dict:
    comps = find_comparables(deal, pool)
    prices = [float(c["price_eur"]) for c in comps if c.get("price_eur") is not None]

    est = _med(prices)
    source = "comparables"
    if est is None:
        est = tier_fallback_median(deal, pool)
        source = "tier-median" if est is not None else "none"

    asking = float(deal["asking_eur"]) if deal.get("asking_eur") is not None else None
    fees = _fee_factor()
    refurb = _refurb(deal.get("tier"))
    profit = None
    if est is not None and asking is not None:
        profit = round(est * fees - asking - refurb, 2)

    deal = dict(deal)
    deal.update({
        "est_resale": round(est, 2) if est is not None else None,
        "est_source": source,
        "n_comps": len(prices),
        "confidence": _conf(len(prices)),
        "refurb_eur": refurb,
        "profit": profit,
        "is_deal": (
            profit is not None
            and profit >= CONFIG.get("min_profit_eur", 50)
            and _conf(len(prices)) in ("med", "high")
        ),
    })
    return deal


def compute_deals(conn, window_days: int = DEAL_WINDOW_DAYS) -> tuple[list[dict], list[dict]]:
    pool = fetch_sold_pool(conn, window_days)
    deals = [score_deal(d, pool) for d in fetch_deal_inputs(conn)]
    deals.sort(key=lambda d: (d["profit"] is None, -(d["profit"] or 0)))
    return deals, pool


# ------------------------------------------------------------------ report

def _fmt_eur(v) -> str:
    return "—" if v is None else f"€{v:,.0f}"


def render_txt(deals: list[dict], pool: list[dict], unscored: list[dict],
               window_days: int) -> str:
    real = [d for d in deals if d["is_deal"]]
    by_conf = {"high": 0, "med": 0, "low": 0}
    for d in deals:
        by_conf[d["confidence"]] += 1

    lines = [
        "skelbiu.lt price tracker — deals report",
        f"generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        f"comparables window: {window_days}d · sold pool: {len(pool)} · "
        f"scored actives: {len(deals)}",
        "",
        f"DEALS (profit ≥ {CONFIG.get('min_profit_eur', 50)}€ & confidence ≥ med): {len(real)}",
        "",
    ]

    if real:
        w = (6, 44, 8, 8, 8, 8)
        hdr = f"{'prof':>{w[0]}}  {'title':<{w[1]}}  {'ask':>{w[2]}}  {'resale':>{w[3]}}  {'n':>{w[4]}}  {'conf':<{w[5]}}"
        lines.append(hdr)
        lines.append("-" * len(hdr))
        for d in real[:40]:
            title = (d["title"] or "")[: w[1]]
            lines.append(
                f"{_fmt_eur(d['profit']):>{w[0]}}  {title:<{w[1]}}  "
                f"{_fmt_eur(d['asking_eur']):>{w[2]}}  {_fmt_eur(d['est_resale']):>{w[3]}}  "
                f"{d['n_comps']:>{w[4]}}  {d['confidence']:<{w[5]}}"
            )
    else:
        lines.append("  (none this run)")

    lines += ["", f"confidence spread: high={by_conf['high']} med={by_conf['med']} low={by_conf['low']}"]

    if unscored:
        lines += [
            "",
            f"UNSCORED MODELS ({len(unscored)}) — no PassMark/TPU match, "
            "excluded from deals:",
        ]
        for u in unscored[:25]:
            model = u.get("canonical_model") or u.get("cpu_model") or u.get("gpu_model") or "?"
            lines.append(f"  - {model!r}  ← {u['title'][:60]}")
            lines.append(f"      fix: python3 benchmarks.py --refresh {model!r}")
        if len(unscored) > 25:
            lines.append(f"  … and {len(unscored) - 25} more")
    return "\n".join(lines) + "\n"


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>skelbiu deals</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:60rem;color:#222}}
 h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:2rem}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{padding:.35rem .6rem;border-bottom:1px solid #ddd;text-align:left}}
 th{{background:#f4f4f4;position:sticky;top:0}}
 .num{{text-align:right;font-variant-numeric:tabular-nums}}
 .deal{{background:#eaf7ea}} .warn{{background:#fff6e0}}
 .muted{{color:#888}} .mono{{font-family:ui-monospace,monospace}}
</style></head><body>
<h1>skelbiu.lt price tracker — deals</h1>
<p class="muted">generated {generated} · comparables window {window}d ·
sold pool {pool} · scored actives {n_deals}</p>

<h2>Asking vs sold price (last {window}d)</h2>
<canvas id="chart" height="90"></canvas>

<h2>Deals (profit ≥ {min_profit}€ &amp; confidence ≥ med): {n_real}</h2>
<table>
<tr><th class="num">profit</th><th>title</th><th class="num">ask</th>
<th class="num">est resale</th><th class="num">comps</th><th>conf</th>
<th>tier</th><th>bottleneck</th></tr>
{rows}
</table>

{unscored_html}

<script>
new Chart(document.getElementById('chart'), {{
  type:'bar',
  data:{{
    labels:{labels},
    datasets:[
      {{label:'asking €',data:{asking},backgroundColor:'#9db4d4'}},
      {{label:'sold/est resale €',data:{resale},backgroundColor:'#8fce8f'}}
    ]
  }},
  options:{{plugins:{{legend:{{position:'bottom'}}}},
            scales:{{y:{{beginAtZero:true}}}}}}
}});
</script>
</body></html>"""


def render_html(deals: list[dict], pool: list[dict], unscored: list[dict],
                window_days: int) -> str:
    real = [d for d in deals if d["is_deal"]]
    rows = []
    for d in deals[:60]:
        cls = "deal" if d["is_deal"] else ""
        url = d.get("url") or ""
        title_html = (f'<a href="{url}" target="_blank" rel="noopener">{d["title"][:70]}</a>'
                      if url else d["title"][:70])
        rows.append(
            f'<tr class="{cls}"><td class="num mono">{_fmt_eur(d["profit"])}</td>'
            f'<td>{title_html}</td>'
            f'<td class="num mono">{_fmt_eur(d["asking_eur"])}</td>'
            f'<td class="num mono">{_fmt_eur(d["est_resale"])}</td>'
            f'<td class="num">{d["n_comps"]}</td><td>{d["confidence"]}</td>'
            f'<td>{d.get("tier") or "—"}</td><td>{d.get("bottleneck") or "—"}</td></tr>'
        )

    # chart: top 15 scored listings by combined score, asking vs est resale
    top = [d for d in deals if d.get("combined_score") is not None][:15]
    labels = json.dumps([(d["title"] or "")[:28] for d in top], ensure_ascii=False)
    asking = json.dumps([d["asking_eur"] or 0 for d in top])
    resale = json.dumps([d["est_resale"] or 0 for d in top])

    if unscored:
        items = "".join(
            f'<li class="mono">{(u.get("canonical_model") or u.get("cpu_model") or u.get("gpu_model") or "?")}'
            f' <span class="muted">← {u["title"][:55]} · '
            f'python3 benchmarks.py --refresh "{u.get("canonical_model") or ""}"</span></li>'
            for u in unscored[:25]
        )
        unscored_html = (f'<h2 class="warn">⚠ unscored models ({len(unscored)})</h2>'
                         f'<ul>{items}</ul>')
    else:
        unscored_html = ""

    return _HTML.format(
        generated=f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        window=window_days, pool=len(pool), n_deals=len(deals),
        n_real=len(real), min_profit=CONFIG.get("min_profit_eur", 50),
        rows="\n".join(rows) or '<tr><td colspan="8" class="muted">none</td></tr>',
        unscored_html=unscored_html,
        labels=labels, asking=asking, resale=resale,
    )


# --------------------------------------------------------------------- main

def run(window_days: int = DEAL_WINDOW_DAYS) -> int:
    conn = _primary_conn()
    try:
        deals, pool = compute_deals(conn, window_days)
        unscored = fetch_unscored(conn)
    finally:
        conn.close()

    txt = render_txt(deals, pool, unscored, window_days)
    html = render_html(deals, pool, unscored, window_days)

    DATA.mkdir(exist_ok=True)
    (DATA / "report.txt").write_text(txt)
    (DATA / "report.html").write_text(html)

    print(txt)
    print(f"wrote {DATA / 'report.txt'}")
    print(f"wrote {DATA / 'report.html'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=DEAL_WINDOW_DAYS,
                    help="comparables lookback window in days (default 90)")
    args = ap.parse_args()
    return run(window_days=args.min_days)


if __name__ == "__main__":
    raise SystemExit(main())
