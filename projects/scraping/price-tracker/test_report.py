"""Offline tests for report.py (profit engine, comparables, confidence).
Run: python3 test_report.py"""

from report import (
    _bucket, _conf, _fee_factor, _refurb, find_comparables,
    tier_fallback_median, score_deal, render_txt, render_html,
)

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"[{'ok ' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def _sold(item_id, price, model=None, combined=None, tier=None):
    return {"item_id": item_id, "price_eur": price, "canonical_model": model,
            "combined_score": combined, "tier": tier}


def run():
    # --- confidence buckets
    check("conf 0", _conf(0), "low")
    check("conf 1", _conf(1), "med")
    check("conf 2", _conf(2), "med")
    check("conf 3", _conf(3), "high")
    check("conf 10", _conf(10), "high")

    # --- ±10% bucket
    check("bucket 100 vs 109", _bucket(100, 109), True)
    check("bucket 100 vs 111", _bucket(100, 111), False)
    check("bucket 100 vs 100", _bucket(100, 100), True)
    check("bucket None side", _bucket(100, None), False)
    check("bucket zero base", _bucket(0, 0), False)

    # --- comparables: exact model beats bucket
    deal = {"canonical_model": "RTX 3060", "combined_score": 100}
    pool = [
        _sold("a", 200, model="RTX 3060", combined=105),
        _sold("b", 400, model="RTX 4090", combined=400),
        _sold("c", 210, model="OTHER", combined=104),   # within ±10% bucket
    ]
    comps = find_comparables(deal, pool)
    ids = sorted(c["item_id"] for c in comps)
    check("comps exact+bucket", ids, ["a", "c"])

    # --- tier fallback when no comps
    check("tier fallback median", tier_fallback_median(
        "mid", [_sold("x", 300, tier="mid"), _sold("y", 500, tier="mid")]), 400.0)
    check("tier fallback none", tier_fallback_median("high", [_sold("x", 300, tier="mid")]), None)

    # --- score_deal: real comparables → high confidence
    d = {"item_id": "1", "title": "Dell", "asking_eur": 300.0,
         "canonical_model": "RTX 3060", "combined_score": 100, "tier": "mid"}
    pool3 = [_sold("a", 600, model="RTX 3060"), _sold("b", 620, model="RTX 3060"),
             _sold("c", 640, model="RTX 3060")]
    r = score_deal(d, pool3)
    check("deal est median", r["est_resale"], 620.0)
    check("deal n_comps", r["n_comps"], 3)
    check("deal conf", r["confidence"], "high")
    # profit = 620*0.9 - 300 - refurb(mid=25) = 558 - 325 = 233
    check("deal profit", r["profit"], round(620 * 0.9 - 300 - 25, 2))
    check("deal is_deal", r["is_deal"], True)

    # --- score_deal: no comps → tier median, low confidence → not a deal
    d2 = {"item_id": "2", "title": "X", "asking_eur": 100.0,
          "canonical_model": "MISSING", "combined_score": 50, "tier": "mid"}
    pool_tier = [_sold("t1", 400, tier="mid")]
    r2 = score_deal(d2, pool_tier)
    check("fallback source", r2["est_source"], "tier-median")
    check("fallback conf low", r2["confidence"], "low")
    check("fallback not a deal", r2["is_deal"], False)

    # --- fee + refurb helpers
    check("fee factor 10%", _fee_factor(), 0.9)
    check("refurb mid", _refurb("mid"), 25)
    check("refurb none", _refurb(None), 0)

    # --- report renders both formats without error
    txt = render_txt([r, r2], pool3, [], 90)
    html = render_html([r, r2], pool3, [], 90)
    check("txt has deal", "€233" in txt or "233" in txt, True)
    check("html has chart", "chart.js" in html, True)
    check("html has table", "<table>" in html, True)

    # --- unscored warning section
    fake = [{"item_id": "U", "title": "Unknown Box", "category": "desktops",
             "device_class": "desktop", "canonical_model": "GeForce RTX 9999",
             "cpu_model": None, "gpu_model": "GeForce RTX 9999",
             "spec_confidence": "high"}]
    txt2 = render_txt([], [], fake, 90)
    html2 = render_html([], [], fake, 90)
    check("txt unscored warn", "UNSCORED MODELS (1)" in txt2, True)
    check("html unscored warn", "unscored models (1)" in html2, True)
    check("html refresh hint", "benchmarks.py --refresh" in html2, True)

    print(f"\n{'PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
