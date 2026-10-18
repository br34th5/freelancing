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


def _value_per_eur(deal: dict) -> float | None:
    """k-points per € at asking price — higher = more bang per buck.

    Mirrors `scoring.value_per_eur()`; computed here from already-fetched
    fields so the report can rank without storing a new DB column. Whole PCs
    use combined_score; components use the larger of cpu_score / gpu_score.
    Returns None when there's no score or asking price.
    """
    price = deal.get("asking_eur")
    if not price or price <= 0:
        return None
    cls = (deal.get("device_class") or "").strip()
    if cls in ("desktop", "laptop", "server"):
        pts_k = deal.get("combined_score")
    else:
        cpu = deal.get("cpu_score") or 0
        gpu = deal.get("gpu_score") or 0
        pts_k = (cpu if cpu > gpu else gpu) / 1000.0
    if not pts_k or pts_k <= 0:
        return None
    return round(pts_k / price, 4)


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
                   s.cpu_model, s.cpu_score, s.gpu_model, s.gpu_score,
                   s.res_brackets,
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


def fetch_bottlenecks(conn) -> list[dict]:
    """Active listings with at least one scoring gap (cpu-missing/gpu-missing).

    Different from `fetch_unscored` (which surfaces benchmark-key gaps): these
    are listings we DID score on one side but couldn't resolve the other.
    Showing the model we DO have alongside the missing side lets the user see
    exactly what would close the gap (e.g. add a benchmark row for the
    missing model).
    """
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT l.item_id, l.title, l.url, l.category, l.location,
                   s.device_class, s.cpu_model, s.cpu_score,
                   s.gpu_model, s.gpu_score, s.combined_score, s.tier,
                   s.bottleneck, s.spec_confidence,
                   (SELECT o.price_eur FROM {SCHEMA}.observations o
                    WHERE o.item_id = l.item_id
                    ORDER BY o.observed_at DESC LIMIT 1) AS asking_eur
            FROM {SCHEMA}.listings l
            JOIN {SCHEMA}.listing_specs s ON s.item_id = l.item_id
            WHERE l.last_state = 'active'
              AND s.bottleneck IN ('cpu-missing', 'gpu-missing')
              AND s.spec_confidence <> 'low'
            ORDER BY s.bottleneck, s.combined_score DESC NULLS LAST
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_nas_cpus(cpu_idx: dict, max_tdp_w: float = 95.0,
                   min_listings: int = 0) -> list[dict]:
    """NAS efficiency table — CPUs sorted by TDP ascending.

    Primary use case: "what low-watt CPU should I use for a NAS build?".
    Surfaces every CPU from cpu_scores that has both a TDP (from
    data/cpu_tdp.json) and a PassMark score, with score-per-watt efficiency
    and the count of active listings currently selling that model.

    max_tdp_w: cap the table at this wattage (default 95W cuts the obvious
    desktop CPUs that aren't NAS-relevant). Set to a much higher value (or
    0) to see every CPU with TDP data.
    min_listings: drop CPUs that don't appear in any active listing. Set to
    0 to keep theoretical-only entries (useful for "what should I build
    with" rather than "what's on the market").
    """
    from specs import _clean  # for normalize

    def _norm(name: str) -> str:
        from specs import normalize_key
        return normalize_key(name)

    out = []
    for key, entry in cpu_idx.items():
        tdp = entry.tdp_w
        mark = entry.cpu_mark
        if tdp is None or mark is None or mark <= 0:
            continue
        if max_tdp_w > 0 and tdp > max_tdp_w:
            continue
        out.append({
            "model": entry.model,
            "cpu_mark": mark,
            "tdp_w": tdp,
            "score_per_watt": round(mark / tdp, 1),
        })
    out.sort(key=lambda r: (r["tdp_w"], -r["score_per_watt"]))
    return out


def fetch_nas_candidates(cpu_idx: dict, listings: list[dict],
                         max_tdp_w: float = 95.0) -> list[dict]:
    """NAS candidates — actual listings whose CPU is NAS-suitable.

    Companion to `fetch_nas_cpus` (which is theoretical / catalog-wide).
    This surfaces what's actually for sale right now: every active listing
    whose CPU has TDP data (from data/cpu_tdp.json) and falls within the
    watt cap, with the asking price so you can see "what does the cheapest
    6 W chip cost me".

    Each row: title (clickable in HTML), cpu_model, asking price, TDP,
    PassMark score, and pts/watt.

    Sort: TDP ascending (least power first), then pts/watt descending
    (most efficient in that band), then asking price ascending (cheapest
    tiebreaker). Same priority as `fetch_nas_cpus` with price added so
    two same-watt, same-efficiency listings rank by cost.
    """
    from specs import normalize_key

    out = []
    for d in listings:
        cpu_model = d.get("cpu_model")
        if not cpu_model:
            continue
        asking = d.get("asking_eur")
        if not asking:
            continue
        entry = cpu_idx.get(normalize_key(cpu_model))
        if entry is None or entry.tdp_w is None or entry.cpu_mark is None:
            continue
        if entry.cpu_mark <= 0:
            continue
        if max_tdp_w > 0 and entry.tdp_w > max_tdp_w:
            continue
        out.append({
            "title": d.get("title") or "",
            "url": d.get("url") or "",
            "asking_eur": asking,
            "cpu_model": cpu_model,
            "tdp_w": entry.tdp_w,
            "cpu_mark": entry.cpu_mark,
            "score_per_watt": round(entry.cpu_mark / entry.tdp_w, 1),
        })
    out.sort(key=lambda r: (r["tdp_w"], -r["score_per_watt"], r["asking_eur"]))
    return out


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
        "value_per_eur": _value_per_eur(deal),
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
    # Primary sort: most profit → least profit. Listings with no profit
    # (missing est_resale / asking) sink to the bottom. Secondary: k-pts
    # per € desc — within a profit band, the better-value listing wins.
    deals.sort(key=lambda d: (
        d["profit"] is None,
        -(d["profit"] or 0),
        d.get("value_per_eur") is None,
        -(d.get("value_per_eur") or 0),
    ))
    return deals, pool


# ------------------------------------------------------------------ report

def _fmt_eur(v) -> str:
    return "—" if v is None else f"€{v:,.0f}"


def render_txt(deals: list[dict], pool: list[dict], unscored: list[dict],
               bottlenecks: list[dict], nas_cpus: list[dict],
               nas_candidates: list[dict], window_days: int) -> str:
    real = [d for d in deals if d["is_deal"]]
    by_conf = {"high": 0, "med": 0, "low": 0}
    by_bn: dict[str, int] = {}
    for d in deals:
        by_conf[d["confidence"]] += 1
        bn = d.get("bottleneck") or "balanced"
        by_bn[bn] = by_bn.get(bn, 0) + 1

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
        w = (6, 44, 8, 8, 8, 8, 9)
        hdr = f"{'prof':>{w[0]}}  {'title':<{w[1]}}  {'ask':>{w[2]}}  {'resale':>{w[3]}}  {'n':>{w[4]}}  {'conf':<{w[5]}}  {'pts/€':>{w[6]}}"
        lines.append(hdr)
        lines.append("-" * len(hdr))
        for d in real[:40]:
            title = (d["title"] or "")[: w[1]]
            vpe = d.get("value_per_eur")
            vpe_str = f"{vpe:.4f}" if vpe is not None else "—"
            lines.append(
                f"{_fmt_eur(d['profit']):>{w[0]}}  {title:<{w[1]}}  "
                f"{_fmt_eur(d['asking_eur']):>{w[2]}}  {_fmt_eur(d['est_resale']):>{w[3]}}  "
                f"{d['n_comps']:>{w[4]}}  {d['confidence']:<{w[5]}}  {vpe_str:>{w[6]}}"
            )
    else:
        lines.append("  (none this run)")

    # value-for-money view: every scored listing ranked best→worst by k-pts
    # per €, regardless of whether it crosses the profit threshold. The
    # "best deal" candidates tend to cluster at the top; listings near the
    # threshold often hide here.
    by_value = sorted(
        (d for d in deals if d.get("value_per_eur") is not None),
        key=lambda d: -d["value_per_eur"],
    )
    if by_value:
        lines += [
            "",
            f"VALUE FOR MONEY — top {min(15, len(by_value))} by k-pts per € (best first):",
            "",
        ]
        w2 = (44, 7, 9, 7, 9)
        hdr2 = f"{'title':<{w2[0]}}  {'ask':>{w2[1]}}  {'pts/€':>{w2[2]}}  {'comps':>{w2[3]}}  {'prof':>{w2[4]}}"
        lines.append(hdr2)
        lines.append("-" * len(hdr2))
        for d in by_value[:15]:
            title = (d["title"] or "")[: w2[0]]
            vpe = d.get("value_per_eur")
            vpe_str = f"{vpe:.4f}" if vpe is not None else "—"
            lines.append(
                f"{title:<{w2[0]}}  {_fmt_eur(d['asking_eur']):>{w2[1]}}  "
                f"{vpe_str:>{w2[2]}}  {d['n_comps']:>{w2[3]}}  "
                f"{_fmt_eur(d['profit']):>{w2[4]}}"
            )

    # NAS candidates — actual listings from `listings` whose CPU has TDP data
    # within the watt cap. Sorted: least watts first, then pts/watt, then
    # cheapest. Reads as "what can I buy today to build a low-power NAS".
    if nas_candidates:
        lines += [
            "",
            "NAS CANDIDATES — active listings with a NAS-suitable CPU "
            "(≤ 95W TDP), best efficiency first:",
            "",
        ]
        w4 = (44, 24, 7, 7, 9, 8)
        hdr4 = (
            f"{'title':<{w4[0]}}  {'cpu':<{w4[1]}}  {'ask':>{w4[2]}}  "
            f"{'tdp_w':>{w4[3]}}  {'cpu_mark':>{w4[4]}}  {'pts/w':>{w4[5]}}"
        )
        lines.append(hdr4)
        lines.append("-" * len(hdr4))
        for c in nas_candidates[:20]:
            title = (c["title"] or "")[: w4[0]]
            cpu = (c["cpu_model"] or "")[: w4[1]]
            lines.append(
                f"{title:<{w4[0]}}  {cpu:<{w4[1]}}  "
                f"{_fmt_eur(c['asking_eur']):>{w4[2]}}  {c['tdp_w']:>{w4[3]}}  "
                f"{c['cpu_mark']:>{w4[4]}.0f}  {c['score_per_watt']:>{w4[5]}.1f}"
            )
        if len(nas_candidates) > 20:
            lines.append(f"  … and {len(nas_candidates) - 20} more")

    # NAS efficiency — low-watt + per-watt perf table. Sort: least watts first
    # (per user request — "least wattage use is priority"); the score/watt
    # column shows efficiency for free.
    if nas_cpus:
        # already sorted by tdp_w asc, score/watt desc
        lines += [
            "",
            "NAS EFFICIENCY — most efficient CPUs (≤ 95W TDP), least wattage first:",
            "",
        ]
        w3 = (42, 8, 8, 9)
        hdr3 = f"{'cpu':<{w3[0]}}  {'tdp_w':>{w3[1]}}  {'cpu_mark':>{w3[2]}}  {'pts/watt':>{w3[3]}}"
        lines.append(hdr3)
        lines.append("-" * len(hdr3))
        for c in nas_cpus[:30]:
            model = c["model"][: w3[0]]
            lines.append(
                f"{model:<{w3[0]}}  {c['tdp_w']:>{w3[1]}}  {c['cpu_mark']:>8.0f}  "
                f"{c['score_per_watt']:>{w3[3]}.1f}"
            )
        if len(nas_cpus) > 30:
            lines.append(f"  … and {len(nas_cpus) - 30} more")

    lines += [
        "",
        f"confidence spread: high={by_conf['high']} med={by_conf['med']} low={by_conf['low']}",
        f"bottleneck spread: "
        + ", ".join(f"{k}={v}" for k, v in sorted(by_bn.items())),
    ]

    # Bottleneck gap analysis: one-sided scoring (cpu-missing / gpu-missing).
    # Each row shows the model we DID resolve + the missing side, so it's
    # obvious what would close the gap (a benchmark row for the missing
    # model — the same hint the unscored-models section gives).
    if bottlenecks:
        cpu_miss = [b for b in bottlenecks if b.get("bottleneck") == "cpu-missing"]
        gpu_miss = [b for b in bottlenecks if b.get("bottleneck") == "gpu-missing"]
        lines += [
            "",
            f"BOTTLENECK GAPS ({len(bottlenecks)}) — one-sided scoring; "
            "refresh the missing model to close the gap:",
            f"  cpu-missing: {len(cpu_miss)}   gpu-missing: {len(gpu_miss)}",
            "",
        ]
        for b in bottlenecks[:30]:
            bn = b.get("bottleneck")
            side = "cpu" if bn == "cpu-missing" else "gpu"
            other = "gpu" if side == "cpu" else "cpu"
            other_model = (b.get(f"{other}_model") or "?")
            other_score = b.get(f"{other}_score")
            other_score_str = f"{other_score:.0f}" if other_score is not None else "—"
            cls = (b.get("device_class") or "?").upper()
            price = _fmt_eur(b.get("asking_eur"))
            title = (b.get("title") or "")[:55]
            lines.append(
                f"  [{cls:>7}] {side:>3}-missing   {other}={other_model} ({other_score_str})   "
                f"{price:>8}   {title}"
            )
            lines.append(
                f"      fix: python3 benchmarks.py --refresh <{side}-model>  ·  "
                f"current {side}='{b.get(f'{side}_model') or '—'}'"
            )
        if len(bottlenecks) > 30:
            lines.append(f"  … and {len(bottlenecks) - 30} more")

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
 .small{{font-size:12px}}
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
<th>tier</th><th>bottleneck</th><th>cpu</th><th>gpu</th><th class="num">k-pts/€</th></tr>
{rows}
</table>

<h2>Value for money — best k-points per € first</h2>
<p class="muted small">k-points per € at asking price. Listings near the top are the most performance per euro spent; listings below the deals-threshold often hide here.</p>
<table>
<tr><th>title</th><th class="num">ask</th><th class="num">k-pts/€</th>
<th class="num">comps</th><th class="num">profit</th><th>tier</th></tr>
{value_rows}
</table>

{nas_cand_html}

{nas_html}

{bottleneck_html}

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
                bottlenecks: list[dict], nas_cpus: list[dict],
                nas_candidates: list[dict], window_days: int) -> str:
    real = [d for d in deals if d["is_deal"]]
    rows = []
    for d in deals[:60]:
        cls = "deal" if d["is_deal"] else ""
        url = d.get("url") or ""
        title_html = (f'<a href="{url}" target="_blank" rel="noopener">{d["title"][:70]}</a>'
                      if url else d["title"][:70])
        cpu_m = (d.get("cpu_model") or "—")
        gpu_m = (d.get("gpu_model") or "—")
        cpu_s = d.get("cpu_score")
        gpu_s = d.get("gpu_score")
        cpu_str = f"{cpu_m}" + (f" ({cpu_s:.0f})" if cpu_s is not None else "")
        gpu_str = f"{gpu_m}" + (f" ({gpu_s:.0f})" if gpu_s is not None else "")
        vpe = d.get("value_per_eur")
        vpe_str = f"{vpe:.4f}" if vpe is not None else "—"
        rows.append(
            f'<tr class="{cls}"><td class="num mono">{_fmt_eur(d["profit"])}</td>'
            f'<td>{title_html}</td>'
            f'<td class="num mono">{_fmt_eur(d["asking_eur"])}</td>'
            f'<td class="num mono">{_fmt_eur(d["est_resale"])}</td>'
            f'<td class="num">{d["n_comps"]}</td><td>{d["confidence"]}</td>'
            f'<td>{d.get("tier") or "—"}</td>'
            f'<td class="mono">{d.get("bottleneck") or "—"}</td>'
            f'<td class="mono small">{cpu_str}</td>'
            f'<td class="mono small">{gpu_str}</td>'
            f'<td class="num mono">{vpe_str}</td></tr>'
        )

    # chart: top 15 scored listings by combined score, asking vs est resale
    top = [d for d in deals if d.get("combined_score") is not None][:15]
    labels = json.dumps([(d["title"] or "")[:28] for d in top], ensure_ascii=False)
    asking = json.dumps([d["asking_eur"] or 0 for d in top])
    resale = json.dumps([d["est_resale"] or 0 for d in top])

    # value-for-money table: every scored listing with a value, ranked by
    # k-points per € (highest first). Built independently from the deals
    # table — both views are useful (a listing can be great value without
    # crossing the profit threshold, e.g. no comparable sold prices).
    by_value = sorted(
        (d for d in deals if d.get("value_per_eur") is not None),
        key=lambda d: -d["value_per_eur"],
    )
    value_rows = []
    for d in by_value[:20]:
        cls = "deal" if d["is_deal"] else ""
        url = d.get("url") or ""
        title_html = (f'<a href="{url}" target="_blank" rel="noopener">{d["title"][:55]}</a>'
                      if url else d["title"][:55])
        vpe = d.get("value_per_eur")
        vpe_str = f"{vpe:.4f}" if vpe is not None else "—"
        value_rows.append(
            f'<tr class="{cls}"><td>{title_html}</td>'
            f'<td class="num mono">{_fmt_eur(d["asking_eur"])}</td>'
            f'<td class="num mono">{vpe_str}</td>'
            f'<td class="num">{d["n_comps"]}</td>'
            f'<td class="num mono">{_fmt_eur(d["profit"])}</td>'
            f'<td>{d.get("tier") or "—"}</td></tr>'
        )

    # bottleneck / gap breakdown — listings scored on one side but missing
    # the other. Shows the model we DO have + the missing side so it's
    # obvious what would close the gap (a benchmark refresh for that model).
    if bottlenecks:
        cpu_miss = [b for b in bottlenecks if b.get("bottleneck") == "cpu-missing"]
        gpu_miss = [b for b in bottlenecks if b.get("bottleneck") == "gpu-missing"]
        bn_rows = []
        for b in bottlenecks[:40]:
            bn = b.get("bottleneck")
            side = "cpu" if bn == "cpu-missing" else "gpu"
            other = "gpu" if side == "cpu" else "cpu"
            other_model = (b.get(f"{other}_model") or "?")
            other_score = b.get(f"{other}_score")
            other_score_str = f"{other_score:.0f}" if other_score is not None else "—"
            url = b.get("url") or ""
            title = (b.get("title") or "")[:55]
            title_html = (f'<a href="{url}" target="_blank" rel="noopener">{title}</a>'
                          if url else title)
            cls = (b.get("device_class") or "?").upper()
            bn_rows.append(
                f'<tr><td>{bn}</td><td class="mono small">{cls}</td>'
                f'<td class="num mono">{_fmt_eur(b.get("asking_eur"))}</td>'
                f'<td class="mono">{other}={other_model} <span class="muted">({other_score_str})</span></td>'
                f'<td>{title_html}</td></tr>'
            )
        bottleneck_html = (
            f'<h2 class="warn">⚠ bottleneck gaps ({len(bottlenecks)}) — '
            f'cpu-missing: {len(cpu_miss)}, gpu-missing: {len(gpu_miss)}</h2>'
            '<table>'
            '<tr><th>gap</th><th>class</th><th class="num">ask</th>'
            '<th>scored side (model · benchmark)</th><th>title</th></tr>'
            + "\n".join(bn_rows) +
            '</table>'
        )
    else:
        bottleneck_html = ""

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

    # NAS efficiency table — low-watt CPUs ranked by TDP ascending. Sort key
    # is TDP first (per user spec) with score/watt as the secondary sort.
    if nas_cpus:
        nas_rows = []
        for c in nas_cpus[:30]:
            nas_rows.append(
                f'<tr><td class="mono">{c["model"]}</td>'
                f'<td class="num mono">{c["tdp_w"]} W</td>'
                f'<td class="num mono">{c["cpu_mark"]:.0f}</td>'
                f'<td class="num mono">{c["score_per_watt"]:.1f}</td></tr>'
            )
        nas_html = (
            '<h2>NAS efficiency — most efficient CPUs</h2>'
            '<p class="muted small">CPUs with known TDP (≤ 95 W), sorted by '
            'least wattage first (priority); pts/watt column shows PassMark '
            'score per watt — higher = more performance per watt.</p>'
            '<table>'
            '<tr><th>cpu</th><th class="num">tdp</th><th class="num">cpu_mark</th>'
            '<th class="num">pts/watt</th></tr>'
            + "\n".join(nas_rows) +
            '</table>'
        )
        if len(nas_cpus) > 30:
            nas_html += f'<p class="muted small">… and {len(nas_cpus) - 30} more</p>'
    else:
        nas_html = ""

    # NAS candidates — active listings with a NAS-suitable CPU (TDP data +
    # within watt cap). Sibling of the theoretical NAS table above: this
    # one answers "what can I buy right now to build a low-power NAS".
    if nas_candidates:
        cand_rows = []
        for c in nas_candidates[:20]:
            url = c.get("url") or ""
            title = (c.get("title") or "")[:55]
            title_html = (f'<a href="{url}" target="_blank" rel="noopener">{title}</a>'
                          if url else title)
            cand_rows.append(
                f'<tr><td>{title_html}</td>'
                f'<td class="mono small">{c["cpu_model"]}</td>'
                f'<td class="num mono">{_fmt_eur(c["asking_eur"])}</td>'
                f'<td class="num mono">{c["tdp_w"]} W</td>'
                f'<td class="num mono">{c["cpu_mark"]:.0f}</td>'
                f'<td class="num mono">{c["score_per_watt"]:.1f}</td></tr>'
            )
        nas_cand_html = (
            '<h2>NAS candidates — best listings for a low-power NAS build</h2>'
            '<p class="muted small">Active listings whose CPU has known TDP '
            '(≤ 95 W). Sorted by least wattage first (priority), then '
            'pts/watt, then cheapest. Click a title to open the listing.</p>'
            '<table>'
            '<tr><th>title</th><th>cpu</th><th class="num">ask</th>'
            '<th class="num">tdp</th><th class="num">cpu_mark</th>'
            '<th class="num">pts/watt</th></tr>'
            + "\n".join(cand_rows) +
            '</table>'
        )
        if len(nas_candidates) > 20:
            nas_cand_html += f'<p class="muted small">… and {len(nas_candidates) - 20} more</p>'
    else:
        nas_cand_html = ""

    return _HTML.format(
        generated=f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        window=window_days, pool=len(pool), n_deals=len(deals),
        n_real=len(real), min_profit=CONFIG.get("min_profit_eur", 50),
        rows="\n".join(rows) or '<tr><td colspan="11" class="muted">none</td></tr>',
        value_rows="\n".join(value_rows) or '<tr><td colspan="6" class="muted">none</td></tr>',
        unscored_html=unscored_html,
        bottleneck_html=bottleneck_html,
        nas_html=nas_html,
        nas_cand_html=nas_cand_html,
        labels=labels, asking=asking, resale=resale,
    )


# --------------------------------------------------------------------- main

def run(window_days: int = DEAL_WINDOW_DAYS) -> int:
    conn = _primary_conn()
    try:
        deals, pool = compute_deals(conn, window_days)
        unscored = fetch_unscored(conn)
        bottlenecks = fetch_bottlenecks(conn)
    finally:
        conn.close()

    # NAS efficiency table — built from cpu_scores + cpu_tdp (no DB needed).
    # Loads a fresh CPU index here so the TDP field populated by
    # load_cpu_index() (from data/cpu_tdp.json) is available.
    from scoring import load_cpu_index
    cpu_idx = load_cpu_index()
    nas_cpus = fetch_nas_cpus(cpu_idx, max_tdp_w=95.0)
    # NAS candidates — same TDP index, cross-referenced with the live
    # active listings so the report shows what's actually for sale.
    nas_candidates = fetch_nas_candidates(cpu_idx, deals, max_tdp_w=95.0)

    txt = render_txt(deals, pool, unscored, bottlenecks, nas_cpus,
                     nas_candidates, window_days)
    html = render_html(deals, pool, unscored, bottlenecks, nas_cpus,
                       nas_candidates, window_days)

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
