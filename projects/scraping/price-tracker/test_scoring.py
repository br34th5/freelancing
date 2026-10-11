"""Offline tests for scoring.py (brackets, genre tiers, whole-PC tier).
Run: python3 test_scoring.py"""

from scoring import (
    _reco_to_brackets, _gpu_bracket_from_g3d, apply_vram_gate, resolve_gpu,
    resolve_cpu, cpu_genre_tiers, tier_for, combined_score, value_score,
    classify_device, score_listing, GpuScore, CpuScore,
)

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"[{'ok ' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def run():
    # --- TPU reco → brackets
    check("reco 4090", _reco_to_brackets(
        [("1920x1080", "Good"), ("2560x1440", "Good"), ("3840x2160", "Good")]),
        ("4K", None))
    check("reco 1650 (no playable standard)", _reco_to_brackets(
        [("640x480", "Playable"), ("1280x720", "Unplayable"), ("1366x768", "Unplayable")]),
        (None, None))
    check("reco 6600xt (720p low fps)", _reco_to_brackets(
        [("640x480", "Good"), ("1280x720", "Low FPS"), ("1366x768", "Unplayable")]),
        (None, None))

    # --- G3D band fallback
    check("g3d 4k", _gpu_bracket_from_g3d(32000), ("4K", None))
    check("g3d 1440p", _gpu_bracket_from_g3d(16885), ("1440p", "4K"))
    check("g3d 1080p", _gpu_bracket_from_g3d(7867), ("1080p", "1440p"))
    check("g3d low", _gpu_bracket_from_g3d(2000), (None, None))
    check("g3d none", _gpu_bracket_from_g3d(None), (None, None))

    # --- VRAM gate
    g = GpuScore(model="x", vram_gb=8)
    check("vram 8 no 4k", apply_vram_gate(g, "4K"), "1440p")
    g2 = GpuScore(model="x", vram_gb=4)
    check("vram 4 no 1440p", apply_vram_gate(g2, "1440p"), "1080p")
    g3 = GpuScore(model="x", vram_gb=None)
    check("vram unknown passthrough", apply_vram_gate(g3, "4K"), "4K")

    # --- resolve
    gpu_idx = {
        "rtx3060": GpuScore(model="GeForce RTX 3060", g3d_mark=16885),
        "rtx3060ti": GpuScore(model="GeForce RTX 3060 Ti", g3d_mark=20000),
        "rtx4090": GpuScore(model="GeForce RTX 4090", g3d_mark=40000,
                            tpu_reco_res="4K"),
        "rtx3070": GpuScore(model="GeForce RTX 3070", g3d_mark=22000,
                            tpu_reco_res="1440p"),
    }
    r = resolve_gpu("GeForce RTX 3060 12GB", gpu_idx)
    check("resolve gpu exact+suffix", r.model, "GeForce RTX 3060")
    check("resolve gpu not sibling", r.model != "GeForce RTX 3060 Ti", True)
    r4 = resolve_gpu("GeForce RTX 4090", gpu_idx)
    check("resolve gpu tpu bracket kept", r4.tpu_reco_res, "4K")

    # --- variant resolution (bare model → suffixed PassMark key)
    var_idx = {
        "rtx5060ti8gb": GpuScore(model="GeForce RTX 5060 Ti 8GB", g3d_mark=20000),
        "rtx5060ti16gb": GpuScore(model="GeForce RTX 5060 Ti 16GB", g3d_mark=22000),
        "rtx4050laptopgpu": GpuScore(model="GeForce RTX 4050 Laptop GPU",
                                     g3d_mark=12000, vram_gb=6),
        "rx9060xt8gb": GpuScore(model="Radeon RX 9060 XT 8GB", g3d_mark=18000),
        "rx9060xt16gb": GpuScore(model="Radeon RX 9060 XT 16GB", g3d_mark=19000),
    }
    r5060 = resolve_gpu("GeForce RTX 5060 Ti", var_idx)
    check("variant 5060ti → smaller capacity", r5060.model, "GeForce RTX 5060 Ti 8GB")
    r9060 = resolve_gpu("Radeon RX 9060 XT", var_idx)
    check("variant 9060xt → smaller capacity", r9060.model, "Radeon RX 9060 XT 8GB")
    # --- 'laptopgpu' variant gating ---
    # only the laptop SKU exists + title is clearly mobile → laptop part used
    lap_only_idx = {"rtx4050laptopgpu": GpuScore(
        model="GeForce RTX 4050 Laptop GPU", g3d_mark=12000, vram_gb=6)}
    r4050_lap = resolve_gpu("RTX 4050", lap_only_idx,
                            title="Asus Tuf Gaming A16 Rtx 4050")
    check("variant 4050 mobile title + laptop-only index → laptop gpu",
          r4050_lap.model, "GeForce RTX 4050 Laptop GPU")
    # only the laptop SKU exists but title is NOT mobile → no silent inherit;
    # falls through to unscored rather than mis-scoring a desktop part
    r4050_desk = resolve_gpu("RTX 4050", lap_only_idx,
                             title="Rtx 4050 zaidimu kompiuteris")
    check("variant 4050 non-mobile title + laptop-only index → None",
          r4050_desk, None)

    cpu_idx = {
        "ryzen77800x3d": CpuScore(model="AMD Ryzen 7 7800X3D", cpu_mark=34279,
                                  st_mark=3759, l3_mb=96),
        "corei512400": CpuScore(model="Intel Core i5-12400", cpu_mark=19000,
                                st_mark=3600, l3_mb=18),
        # key must match specs.normalize_key output ('i513600k', not 'corei513600k')
        "i513600k": CpuScore(model="Intel Core i5-13600K", cpu_mark=28000,
                             st_mark=4100, l3_mb=24),
    }
    c = resolve_cpu("AMD Ryzen 7 7800X3D", cpu_idx)
    check("resolve cpu", c.cpu_mark, 34279)

    # --- genre tiers + x3d bonus
    tiers = cpu_genre_tiers(cpu_idx["ryzen77800x3d"])
    check("genres present", sorted(tiers), ["aaa", "esports", "mmo", "sim", "strategy"])
    check("x3d mmo high", tiers["mmo"] >= tiers["esports"], True)

    # --- whole-PC tier + bottleneck
    tier, bn = tier_for("1440p", {"aaa": 80}, genre="aaa")
    check("gpu-limited tier", (tier, bn), ("mid", "gpu"))
    tier2, bn2 = tier_for("4K", {"aaa": 30}, genre="aaa")
    check("cpu-limited tier", (tier2, bn2), ("entry", "cpu"))
    # 1080p cap (0) is stricter than a strong CPU (aaa=90 → cap 2),
    # so GPU is the binding constraint and the tier is floored at entry.
    tier3, bn3 = tier_for("1080p", {"aaa": 90}, genre="aaa")
    check("balanced floors to stricter side", (tier3, bn3), ("entry", "gpu"))
    # exactly equal caps (1440p cap 1 == cpu 50 → cap 1) → no bottleneck
    tie = tier_for("1440p", {"aaa": 50}, genre="aaa")
    check("tie caps no bottleneck", tie, ("mid", None))
    tier4, bn4 = tier_for(None, {"aaa": 90}, genre="aaa")
    check("no bracket", (tier4, bn4), (None, "gpu-missing"))

    # --- combined + value
    comb = combined_score("desktop", 34279, 16885, 34279, 16885)
    check("combined desktop", comb, round(0.45 * 34.279 + 0.55 * 16.885, 2))
    vs = value_score(900, comb, 34279, 16885, "desktop")
    check("value desktop", vs, round(900 / comb, 2))
    comp_vs = value_score(200, None, None, 16885, "component")
    check("value component gpu", comp_vs, round(200 / 16.885, 2))

    # --- device class
    check("class laptop", classify_device("laptops", "Lenovo Legion Pro 7 i9 4090"), "laptop")
    check("class cpu cat", classify_device("cpus", "AMD Ryzen 5 5600"), "component")
    check("class gpu cat", classify_device("gpus", "RTX 3060"), "component")
    check("class desktop", classify_device("desktops", "Stacionarus kompiuteris i5"), "desktop")
    check("class server", classify_device("servers", "Serveris Dell PowerEdge"), "server")
    # category is authoritative — a title keyword must not override it
    check("class laptop beats gaming title",
          classify_device("laptops", "Asus Tuf Gaming A16 Rtx 5050 8gb"), "laptop")
    check("class laptop beats no keyword",
          classify_device("laptops", "Hp Pavilion Gaming 15"), "laptop")
    check("class desktop from gaming_pcs",
          classify_device("gaming_pcs", "Zaidimu kompiuteris Rtx 3060"), "desktop")
    # unknown category falls back to title keywords
    check("class unknown cat laptop",
          classify_device("watch:lenovo-legion", "Lenovo Legion 7 Pro"), "laptop")
    check("class unknown cat component",
          classify_device("watch:rtx-3060", "RTX 3060 12GB"), "component")

    # --- end-to-end score_listing (whole PC)
    listing = {"item_id": "1", "title": "Lenovo Legion Pro 7 i9 4090 32gb",
               "category": "laptops", "price_eur": 1700.0}
    res = score_listing(listing, cpu_idx, gpu_idx)
    check("e2e device", res.device_class, "laptop")
    check("e2e gpu_model", res.gpu_model, "GeForce RTX 4090")
    check("e2e bracket", res.res_brackets["primary"], "4K")
    check("e2e tier", res.tier is not None, True)

    # --- component listing (no tier)
    comp = {"item_id": "2", "title": "RTX 3060 12GB", "category": "gpus",
            "price_eur": 200.0}
    cres = score_listing(comp, cpu_idx, gpu_idx)
    check("comp device", cres.device_class, "component")
    check("comp no tier", cres.tier, None)
    check("comp value", cres.value_score, round(200 / 16.885, 2))

    # --- one-sided whole-PC still gets a tier + gap flag
    # NOTE: fixtures pass their own idx; '13600k' resolves via exact key.
    cpu_only = {"item_id": "3", "title": "I5-13600k kompiuteris",
                "category": "desktops", "price_eur": 500.0}
    c3 = score_listing(cpu_only, cpu_idx, gpu_idx)
    check("cpu-only pc tier set", c3.tier is not None, True)
    check("cpu-only pc flag", c3.bottleneck, "gpu-missing")
    gpu_only = {"item_id": "4", "title": "Zaidimu kompiuteris *Rtx 3070*",
                "category": "gaming_pcs", "price_eur": 800.0}
    c4 = score_listing(gpu_only, cpu_idx, gpu_idx)
    check("gpu-only pc tier set", c4.tier is not None, True)
    check("gpu-only pc flag", c4.bottleneck, "cpu-missing")

    print(f"\n{'PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
