"""Offline tests for report.py (profit engine, comparables, confidence).
Run: python3 test_report.py"""

from report import (
    _bucket, _conf, _fee_factor, _refurb, find_comparables,
    tier_fallback_median, score_deal, render_txt, render_html,
    _value_per_eur,
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
            "combined_score": combined, "tier": tier, "device_class": "desktop"}


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
        {"tier": "mid"}, [_sold("x", 300, tier="mid"), _sold("y", 500, tier="mid")]), 400.0)
    check("tier fallback none", tier_fallback_median(
        {"tier": "high"}, [_sold("x", 300, tier="mid")]), None)

    # --- score_deal: real comparables → high confidence
    d = {"item_id": "1", "title": "Dell", "asking_eur": 300.0,
         "canonical_model": "RTX 3060", "combined_score": 100, "tier": "mid",
         "device_class": "desktop", "cpu_score": 18000, "gpu_score": 16885}
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
          "canonical_model": "MISSING", "combined_score": 50, "tier": "mid",
          "device_class": "desktop", "cpu_score": 15000, "gpu_score": 12000}
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
    txt = render_txt([r, r2], pool3, [], [], [], [], 90)
    html = render_html([r, r2], pool3, [], [], [], [], 90)
    check("txt has deal", "€233" in txt or "233" in txt, True)
    check("html has chart", "chart.js" in html, True)
    check("html has table", "<table>" in html, True)
    # new: per-row cpu/gpu columns + bottleneck spread summary
    check("txt bottleneck spread", "bottleneck spread:" in txt, True)
    check("html cpu column", "<th>cpu</th>" in html, True)
    check("html gpu column", "<th>gpu</th>" in html, True)
    # value-for-money column + section
    check("txt pts/€ column header", "pts/€" in txt, True)
    check("html k-pts/€ column header", "k-pts/€" in html, True)
    check("txt value section", "VALUE FOR MONEY" in txt, True)
    check("html value section", "Value for money" in html, True)
    # NAS efficiency (empty list = no section)
    check("no nas section when list empty", "NAS EFFICIENCY" not in txt, True)
    check("no nas section in html when empty", "NAS efficiency" not in html, True)

    # --- NAS efficiency section with sample CPUs ---
    from scoring import CpuScore
    nas_cpus = [
        {"model": "Intel Celeron N100 @ 0.80GHz", "cpu_mark": 5500, "tdp_w": 6,  "score_per_watt": 916.7},
        {"model": "AMD Ryzen 5 5600",              "cpu_mark": 21000,"tdp_w": 65, "score_per_watt": 323.1},
        {"model": "AMD Athlon 3000G",              "cpu_mark": 4400, "tdp_w": 35, "score_per_watt": 125.7},
        {"model": "Intel Core i5-12400",           "cpu_mark": 18824,"tdp_w": 65, "score_per_watt": 289.6},
    ]
    txt_nas = render_txt([], [], [], [], nas_cpus, [], 90)
    html_nas = render_html([], [], [], [], nas_cpus, [], 90)
    check("txt NAS section header", "NAS EFFICIENCY" in txt_nas, True)
    check("txt NAS sorted by tdp (Celeron first)", txt_nas.find("Intel Celeron N100") < txt_nas.find("AMD Ryzen 5 5600"), True)
    check("txt NAS pts/watt column", "pts/watt" in txt_nas, True)
    check("txt NAS Celeron 6W shown", "6" in txt_nas, True)
    check("html NAS section", "NAS efficiency" in html_nas, True)
    check("html NAS Celeron row", "Intel Celeron N100" in html_nas, True)

    # --- NAS candidates section: actual listings with NAS-suitable CPUs ---
    from report import fetch_nas_candidates
    from scoring import load_cpu_index
    cpu_idx = load_cpu_index()
    cand_listings = [
        {"title": "Dell Vostro 5625U", "url": "https://example.test/1",
         "cpu_model": "AMD Ryzen 5 5625U", "asking_eur": 99.0},
        {"title": "Celeron N3010 ultra-low", "url": "https://example.test/2",
         "cpu_model": "Intel Celeron N3010 @ 1.04GHz", "asking_eur": 80.0},
        {"title": "Ryzen 7 1700 bargain", "url": "https://example.test/3",
         "cpu_model": "AMD Ryzen 7 1700", "asking_eur": 15.0},
        {"title": "Bare mobo, no CPU", "url": "https://example.test/4",
         "cpu_model": None, "asking_eur": 50.0},  # → skipped (no cpu_model)
        {"title": "i9-13900K overkill", "url": "https://example.test/5",
         "cpu_model": "Intel Core i9-13900K", "asking_eur": 600.0},  # > 95W → skipped
    ]
    candidates = fetch_nas_candidates(cpu_idx, cand_listings, max_tdp_w=95.0)
    check("candidates: skipped no-cpu listing",
          not any(c["title"] == "Bare mobo, no CPU" for c in candidates), True)
    check("candidates: skipped over-watt listing",
          not any(c["title"] == "i9-13900K overkill" for c in candidates), True)
    check("candidates: includes low-watt Celeron",
          any(c["cpu_model"] == "Intel Celeron N3010 @ 1.04GHz" for c in candidates), True)
    # Sort: tdp asc, pts/w desc, ask asc → Celeron N3010 (4W) should be first
    check("candidates: Celeron N3010 ranks first",
          candidates[0]["cpu_model"] == "Intel Celeron N3010 @ 1.04GHz", True)
    txt_cand = render_txt([], [], [], [], [], candidates, 90)
    html_cand = render_html([], [], [], [], [], candidates, 90)
    check("txt NAS CANDIDATES section", "NAS CANDIDATES" in txt_cand, True)
    check("html NAS candidates header", "NAS candidates" in html_cand, True)
    check("txt candidates shows title", "Dell Vostro 5625U" in txt_cand, True)
    check("html candidates shows title link",
          "example.test/1" in html_cand, True)
    check("html candidates shows tdp/cpu_mark columns",
          "<th class=\"num\">tdp</th>" in html_cand and
          "<th class=\"num\">cpu_mark</th>" in html_cand, True)
    check("candidates: empty list → no section",
          "NAS CANDIDATES" not in render_txt([], [], [], [], [], [], 90), True)

    # --- _value_per_eur helper: pure mirror of scoring.value_per_eur
    vpe_desktop = _value_per_eur({"asking_eur": 900, "combined_score": 24.71,
                                  "device_class": "desktop"})
    check("vpe desktop ≈ 0.0275", round(vpe_desktop, 4), 0.0275)
    vpe_comp = _value_per_eur({"asking_eur": 200, "cpu_score": 19000,
                               "gpu_score": 16885, "device_class": "component"})
    check("vpe component picks larger mark", vpe_comp, round(19 / 200, 4))
    check("vpe no price → None",
          _value_per_eur({"asking_eur": 0, "combined_score": 20,
                          "device_class": "desktop"}), None)
    check("vpe no score → None",
          _value_per_eur({"asking_eur": 500, "combined_score": None,
                          "device_class": "desktop"}), None)

    # --- sort: deals with the same kind of pool get ranked by value first
    rich = dict(r, asking_eur=200.0)               # cheap = higher vpe
    poor = dict(r, asking_eur=2000.0)              # expensive = lower vpe
    rich_scored = score_deal(rich, pool3)
    poor_scored = score_deal(poor, pool3)
    check("cheap vpe > expensive vpe",
          rich_scored["value_per_eur"] > poor_scored["value_per_eur"], True)

    # --- bottleneck gap analysis (cpu-missing / gpu-missing) ---
    bn = [
        {"item_id": "B1", "title": "Bare Rtx 3070 kompiuteris",
         "url": "https://www.skelbiu.lt/x.html", "category": "gaming_pcs",
         "device_class": "desktop", "spec_confidence": "high",
         "bottleneck": "cpu-missing",
         "cpu_model": None, "cpu_score": None,
         "gpu_model": "GeForce RTX 3070", "gpu_score": 22000,
         "asking_eur": 900.0},
        {"item_id": "B2", "title": "Lenovo Ryzen 5 5600H laptopas",
         "url": "https://www.skelbiu.lt/y.html", "category": "laptops",
         "device_class": "laptop", "spec_confidence": "high",
         "bottleneck": "gpu-missing",
         "cpu_model": "AMD Ryzen 5 5600H", "cpu_score": 18000,
         "gpu_model": None, "gpu_score": None,
         "asking_eur": 350.0},
    ]
    txt_bn = render_txt([], [], [], bn, [], [], 90)
    html_bn = render_html([], [], [], bn, [], [], 90)
    check("txt bottleneck header", "BOTTLENECK GAPS (2)" in txt_bn, True)
    check("txt bottleneck cpu/gpu counts",
          "cpu-missing: 1" in txt_bn and "gpu-missing: 1" in txt_bn, True)
    check("txt bottleneck shows scored side",
          "gpu=GeForce RTX 3070" in txt_bn, True)
    check("txt bottleneck shows missing side fix",
          "current cpu='—'" in txt_bn or "current cpu='None'" in txt_bn, True)
    check("html bottleneck header", "bottleneck gaps (2)" in html_bn, True)
    check("html bottleneck counts", "cpu-missing: 1, gpu-missing: 1" in html_bn, True)
    check("html bottleneck shows scored side",
          "gpu=GeForce RTX 3070" in html_bn, True)

    # --- unscored warning section
    fake = [{"item_id": "U", "title": "Unknown Box", "category": "desktops",
             "device_class": "desktop", "canonical_model": "GeForce RTX 9999",
             "cpu_model": None, "gpu_model": "GeForce RTX 9999",
             "spec_confidence": "high"}]
    txt2 = render_txt([], [], fake, [], [], [], 90)
    html2 = render_html([], [], fake, [], [], [], 90)
    check("txt unscored warn", "UNSCORED MODELS (1)" in txt2, True)
    check("html unscored warn", "unscored models (1)" in html2, True)
    check("html refresh hint", "benchmarks.py --refresh" in html2, True)

    print(f"\n{'PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
