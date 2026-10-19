"""Scoring (P4): GPU resolution brackets, CPU genre tiers, whole-PC tier.

Inputs:  listings (DB) + data/{cpu_scores,gpu_scores,tpu_reco}.json (bi-monthly)
Output:  listing_specs table (via database.save_listing_specs)

Device class:
  cpu/gpu component (category cpus|gpus, or title has no device keyword)
    → score that component alone; tier=None (no whole-PC tier)
  whole PC (laptop|desktop|gaming|server) → combined score + min(bracket,genre) tier

GPU bracket resolution order:
  1. TPU recommended-resolution cache (data/tpu_reco.json), quality-ordered
  2. VRAM gate from config (needs a vram_gb — unknown here, so gate is a no-op
     without VRAM; kept for when VRAM is populated)
  3. G3D-band fallback (g3d_bands in config.json, calibrated after first load)

CLI:
  python3 -m scoring                 # score all listings → listing_specs
  python3 -m scoring --calibrate     # print value/combined distribution to set tier_thresholds
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from database import connect, ensure_schema, _targets, SCHEMA, _jsonb, save_listing_specs
from specs import analyze_title, normalize_key

DATA = Path(__file__).parent / "data"
CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())

# TPU reco quality → ordinal (higher = more capable). "Unplayable" excluded.
_QUALITY_ORDER = ["Low FPS", "Playable", "Good"]
_RES_ORDER = ["1920x1080", "2560x1440", "3840x2160"]


@dataclass
class GpuScore:
    model: str
    g3d_mark: float | None = None
    tpu_reco_res: str | None = None      # primary bracket label e.g. "1440p"
    tpu_secondary_res: str | None = None
    vram_gb: float | None = None
    g3d_fallback: bool = False


@dataclass
class CpuScore:
    model: str
    cpu_mark: float | None = None
    st_mark: float | None = None
    l3_mb: float | None = None
    tdp_w: float | None = None      # TDP from data/cpu_tdp.json (NAS efficiency ranking)


@dataclass
class ScoreResult:
    device_class: str
    cpu_model: str | None
    cpu_score: float | None
    gpu_model: str | None
    gpu_score: float | None
    canonical_model: str | None
    res_brackets: dict | None
    genre_tiers: dict | None
    bottleneck: str | None
    combined_score: float | None
    tier: str | None
    value_score: float | None
    value_per_eur: float | None
    spec_confidence: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(name: str) -> dict:
    p = DATA / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ---------------------------------------------------------------- lookups

def load_cpu_index() -> dict[str, CpuScore]:
    raw = _load_json("cpu_scores")
    tdp = _load_json("cpu_tdp")  # may be empty {} if data/cpu_tdp.json missing
    idx: dict[str, CpuScore] = {}
    for name, v in raw.items():
        idx[normalize_key(name)] = CpuScore(
            model=name,
            cpu_mark=v.get("cpu_mark"),
            st_mark=v.get("st_mark"),
            l3_mb=v.get("l3_mb"),
            tdp_w=tdp.get(name),
        )
    return idx


def load_gpu_index() -> dict[str, GpuScore]:
    scores = _load_json("gpu_scores")
    reco = _load_json("tpu_reco")
    idx: dict[str, GpuScore] = {}
    for name, v in scores.items():
        g = GpuScore(model=name, g3d_mark=v.get("g3d_mark"))
        r = reco.get(name, {})
        primary, secondary = _reco_to_brackets(r.get("reco"))
        g.tpu_reco_res, g.tpu_secondary_res = primary, secondary
        idx[normalize_key(name)] = g
    return idx


def _reco_to_brackets(reco: list | None) -> tuple[str | None, str | None]:
    """TPU reco [(res, quality)...] → (primary_label, secondary_label).

    primary = highest resolution whose best quality >= Playable.
    secondary = next-higher standard res, only if primary exists (marked
    'playable (low end of bracket)' by caller semantics).
    """
    if not reco:
        return None, None
    best: dict[str, int] = {}
    for res, quality in reco:
        if quality in _QUALITY_ORDER:
            best[res] = max(best.get(res, -1), _QUALITY_ORDER.index(quality))
    # playable+ resolutions, standard set only
    playable = [r for r in _RES_ORDER if best.get(r, -1) >= _QUALITY_ORDER.index("Playable")]
    if not playable:
        return None, None
    primary = playable[-1]           # highest playable
    label = {"1920x1080": "1080p", "2560x1440": "1440p", "3840x2160": "4K"}[primary]
    higher = [r for r in _RES_ORDER if r > primary]
    secondary = None
    if higher:
        nxt = higher[0]
        secondary = {"1920x1080": "1080p", "2560x1440": "1440p", "3840x2160": "4K"}[nxt]
    return label, secondary


def _gpu_bracket_from_g3d(g3d: float | None) -> tuple[str | None, str | None]:
    if g3d is None:
        return None, None
    bands = CONFIG.get("g3d_bands", {"1440p_min": 12000, "1080p_min": 5000})
    if g3d >= bands.get("4k_min", 30000):
        return "4K", None
    if g3d >= bands.get("1440p_min", 12000):
        return "1440p", "4K"
    if g3d >= bands.get("1080p_min", 5000):
        return "1080p", "1440p"
    return None, None


def apply_vram_gate(g: GpuScore, primary: str | None) -> str | None:
    """VRAM gate overrides TPU/band bracket when VRAM is known."""
    if primary is None or g.vram_gb is None:
        return primary
    gates = CONFIG.get("vram_gates", {})
    if primary == "4K" and g.vram_gb < gates.get("no_4k_below_gb", 10):
        return "1440p"
    if primary == "1440p" and g.vram_gb < gates.get("no_1440p_below_gb", 6):
        return "1080p"
    return primary


# capacity/clock tokens that may be glued onto a model key by normalize_key
# ('rtx306012gb'). VRAM sizes are a small known set; match them as a whole
# token so we never eat the SKU digits ('3060' + '12gb', not '306012' + 'gb').
_CAP_TOKEN = re.compile(r"(?:2|3|4|6|8|10|12|16|20|24|32|48|64|80|128|256)"
                        r"(?:gb|mb|ghz|mhz|hz)$")


def _resolve_fallback(key: str, idx: dict):
    """Exact match failed — try stripping a trailing capacity/clock token that
    normalize_key glued on ('rtx306012gb' → 'rtx3060'). Never matches a
    different SKU ('3060ti')."""
    m = _CAP_TOKEN.search(key)
    if not m or m.start() == 0:
        return None
    stripped = key[: m.start()]
    # require the base to end in a digit so we don't fall back to a bare prefix
    if not stripped[-1].isdigit():
        return None
    return idx.get(stripped)


def _is_mobile_title(title: str) -> bool:
    """True when a title looks like a laptop / mobile GPU listing.

    Explicit keywords first, then a broad device-class catch: gaming-brand
    lines (tuf, legion, predator, omen, …) almost always denote laptops on
    skelbiu, so a bare 'Asus Tuf Gaming A16 Rtx 4050' should resolve to the
    'Laptop GPU' PassMark SKU rather than stay unscored.
    """
    t = title.lower()
    if any(w in t for w in ("laptop", "notebook", "nesiojam", "mobile",
                            "max-q", "maxq")):
        return True
    return classify_device("", title) == "laptop"


def _variant_entry(key: str, idx: dict, title: str):
    """Resolve a bare model against PassMark keys that carry an extra suffix
    ('rtx5060ti' → 'rtx5060ti8gb'/'…16gb'; 'rtx4050' → 'rtx4050laptopgpu').

    Pure capacity variants resolve deterministically to the smaller size. The
    'laptopgpu' variant is used only when the listing title signals a mobile
    part, so a desktop 'RTX 4050' never gets the laptop SKU's score.
    """
    cands = {k[len(key):]: idx[k] for k in idx if k.startswith(key) and k != key}
    if not cands:
        return None
    suffixes = set(cands)
    if all(s.isdigit() for s in suffixes):          # VRAM variants: 8gb/16gb
        return cands[min(suffixes, key=lambda s: int(s))]
    if "laptopgpu" in suffixes:                     # mobile SKU variant
        if _is_mobile_title(title):
            return cands["laptopgpu"]
        for sfx, entry in cands.items():
            if sfx != "laptopgpu":
                return entry
        # only the laptop SKU exists in the index — a non-mobile title must
        # NOT silently inherit the laptop part's score; let the caller fall
        # through to the G3D/unknown path so the listing stays unscored
        # rather than mis-scored.
        return None
    return next(iter(cands.values()))               # any single variant


_X3D_TIERS = ("5", "7", "9")  # AMD made X3D SKUs at every tier


def _x3d_tier_swap(model: str, cpu_idx: dict):
    """For X3D models where the tier guess is wrong, try every tier.

    The X3D regex in specs.py can only match the bare number, so it has to
    guess the tier from the first digit. Real SKUs disagree — "5800X3D" is
    Ryzen 7, "9800X3D" is Ryzen 7, "9900X3D" is Ryzen 9 — so an entry like
    "AMD Ryzen 5 5800X3D" might miss even though "AMD Ryzen 7 5800X3D"
    exists in cpu_scores. Try each tier in turn; return whichever matches.
    """
    m = re.match(r"^AMD Ryzen (\d) (\d{4}X3D)$", model or "")
    if not m:
        return None
    base = m.group(2)
    for tier in _X3D_TIERS:
        candidate = f"AMD Ryzen {tier} {base}"
        if normalize_key(candidate) in cpu_idx:
            return cpu_idx[normalize_key(candidate)]
    return None


def resolve_gpu(model: str | None, gpu_idx: dict[str, GpuScore],
                title: str = "") -> GpuScore | None:
    if not model:
        return None
    key = normalize_key(model)
    g = (gpu_idx.get(key) or _resolve_fallback(key, gpu_idx)
         or _variant_entry(key, gpu_idx, title.lower()))
    if g is None:
        return None
    primary, secondary = g.tpu_reco_res, g.tpu_secondary_res
    if primary is None:
        primary, secondary = _gpu_bracket_from_g3d(g.g3d_mark)
        g.g3d_fallback = True
    primary = apply_vram_gate(g, primary)
    return GpuScore(model=g.model, g3d_mark=g.g3d_mark, tpu_reco_res=primary,
                    tpu_secondary_res=secondary, vram_gb=g.vram_gb,
                    g3d_fallback=g.g3d_fallback)


def resolve_cpu(model: str | None, cpu_idx: dict[str, CpuScore],
                title: str = "") -> CpuScore | None:
    if not model:
        return None
    key = normalize_key(model)
    return (cpu_idx.get(key) or _resolve_fallback(key, cpu_idx)
            or _variant_entry(key, cpu_idx, title.lower())
            or _x3d_tier_swap(model, cpu_idx))


# ---------------------------------------------------------------- scoring

_RES_CAP = {"1080p": 0, "1440p": 1, "4K": 2}


def cpu_genre_tiers(c: CpuScore) -> dict | None:
    """Per-genre 0–100 tier from ST/MT/L3 percentiles + X3D bonus."""
    if c is None or c.cpu_mark is None:
        return None
    # percentile proxies: normalize against a high-end ref (calibratable)
    ref = CONFIG.get("genre_ref", {"st": 4500, "mt": 40000, "l3": 64})
    st_pct = min(100.0, (c.st_mark or 0) / ref["st"] * 100)
    mt_pct = min(100.0, c.cpu_mark / ref["mt"] * 100)
    l3_pct = min(100.0, (c.l3_mb or 0) / ref["l3"] * 100)
    x3d = "x3d" in normalize_key(c.model)
    bonus = CONFIG.get("x3d_bonus", 1.15) if x3d else 1.0
    out = {}
    for genre, w in CONFIG.get("genre_weights", {}).items():
        score = (w["st"] * st_pct + w["mt"] * mt_pct + w["l3"] * l3_pct) * bonus
        out[genre] = round(min(100.0, score), 1)
    return out


def _bracket_cap(res: str | None) -> int | None:
    return _RES_CAP.get(res) if res else None


def tier_for(bracket: str | None, genre_tiers: dict | None, genre: str = "aaa") -> tuple[str | None, str | None]:
    """Whole-PC tier = min(GPU bracket, CPU genre tier) with bottleneck flag."""
    gcap = _bracket_cap(bracket)
    if genre_tiers is None or genre not in genre_tiers:
        return (None, None) if gcap is None else (_cap_to_tier(gcap), "cpu-missing")
    cscore = genre_tiers[genre]
    ccaps = _score_to_caps(cscore)
    if gcap is None:
        return None, "gpu-missing"
    # min of the two capabilities
    final = min(gcap, ccaps)
    if final != gcap:
        bottleneck = "cpu"      # CPU is the stricter side
    elif final != ccaps:
        bottleneck = "gpu"      # GPU is the stricter side
    else:
        bottleneck = None       # balanced — neither is the binding constraint
    return _cap_to_tier(final), bottleneck


def _score_to_caps(score: float) -> int:
    th = CONFIG.get("tier_thresholds", {"entry_max": 40, "mid_max": 65})
    if score < th["entry_max"]:
        return 0
    if score < th["mid_max"]:
        return 1
    return 2


def _cap_to_tier(cap: int) -> str:
    return {0: "entry", 1: "mid", 2: "high"}[cap]


def combined_score(device_class: str, cpu_score: float | None, gpu_score: float | None,
                   cpu_mark: float | None, g3d: float | None) -> float | None:
    w = CONFIG.get("combined_weights", {}).get(device_class)
    if not w:
        return None
    cpu_n = (cpu_mark or 0) / 1000.0
    gpu_n = (g3d or 0) / 1000.0
    if cpu_n == 0 and gpu_n == 0:
        return None
    return round(w["cpu"] * cpu_n + w["gpu"] * gpu_n, 2)


def value_score(price: float | None, combined: float | None, cpu_mark: float | None,
                g3d: float | None, device_class: str) -> float | None:
    """€ per 1000 points: whole PC uses combined; components their own mark."""
    if not price or price <= 0:
        return None
    if device_class in ("desktop", "laptop", "server"):
        pts = combined
    else:
        pts = (cpu_mark / 1000.0) if (cpu_mark or 0) > (g3d or 0) else ((g3d or 0) / 1000.0)
    if not pts:
        return None
    return round(price / pts, 2)


def value_per_eur(price: float | None, combined: float | None, cpu_mark: float | None,
                  g3d: float | None, device_class: str) -> float | None:
    """k-points per € spent at asking price — HIGHER = more value for money.

    Inverse of `value_score` (which is € per 1k pts). value_score=36 means
    the listing gets ~0.028 k-pts per €; this column surfaces that ratio
    directly so the report can rank "most value → least value" without
    mental inversion. Whole PCs use combined; components use the larger of
    cpu_mark/g3d.
    """
    if not price or price <= 0:
        return None
    if device_class in ("desktop", "laptop", "server"):
        pts_k = combined
    else:
        pts_k = (cpu_mark / 1000.0) if (cpu_mark or 0) > (g3d or 0) else ((g3d or 0) / 1000.0)
    if not pts_k or pts_k <= 0:
        return None
    return round(pts_k / price, 4)


def classify_device(category: str, title: str) -> str:
    """Category is authoritative; title keywords only fill the gaps.

    A title keyword can never override the source category — 'Asus Tuf Gaming
    A16' sits in `laptops` and must stay a laptop even though 'gaming' would
    otherwise map it to desktop, and a Threadripper bench in `laptops` is a
    classification error in the source data, not a reason to re-label.
    """
    import re
    from specs import _clean

    by_category = {
        "cpus": "component",
        "gpus": "component",
        "laptops": "laptop",
        "servers": "server",
        "desktops": "desktop",
        "gaming_pcs": "desktop",
    }
    if category in by_category:
        return by_category[category]

    t = _clean(title).lower()
    if re.search(r"\b(nesiojam\w*|laptop|notebook|legion|zenbook|spectre|alienware|predator|nitro|tuf|rog|ideapad|thinkpad|aspire|swift|vivobook|latitude|precision|elitebook|probook|envy|ommen)\b", t):
        return "laptop"
    if re.search(r"\b(serveris|server)\b", t):
        return "server"
    if re.search(r"\b(zaidimu|gaming|stacionarus|kompiuteris|desktop)\b", t):
        return "desktop"
    return "component"


def score_listing(listing: dict, cpu_idx: dict, gpu_idx: dict,
                  title_spec=None) -> ScoreResult:
    title = listing.get("title", "")
    category = listing.get("category", "")

    # Use enriched_title (from detail-page description parsing) when available,
    # falling back to the raw title. Also merge desc_cpu/desc_gpu as text hints
    # so a plain-prose description ("rx vega 56") feeds analyze_title's regex.
    effective_title = listing.get("enriched_title") or title
    desc_cpu = listing.get("desc_cpu", "")
    desc_gpu = listing.get("desc_gpu", "")
    if desc_cpu or desc_gpu:
        extra = " ".join(filter(None, [desc_cpu, desc_gpu]))
        if extra and extra not in effective_title:
            effective_title = f"{effective_title} {extra}"

    if title_spec is None:
        title_spec = analyze_title(effective_title, CONFIG)
    device_class = classify_device(category, title)

    # If the title's regex missed a model but the description parser already
    # extracted one, prefer the description model. This catches plain-prose
    # descriptions that the title-only analyze_title pass skips, and it's
    # what closes the cpu-missing / gpu-missing gap after the spider's
    # detail-page enrichment.
    if desc_cpu and not title_spec.cpus:
        title_spec.cpus = [desc_cpu]
    if desc_gpu and not title_spec.gpus:
        title_spec.gpus = [desc_gpu]

    cpu_model = title_spec.cpus[0] if title_spec.cpus else None
    gpu_model = title_spec.gpus[0] if title_spec.gpus else None
    cpu = resolve_cpu(cpu_model, cpu_idx, effective_title)
    gpu = resolve_gpu(gpu_model, gpu_idx, effective_title)

    cpu_score = cpu.cpu_mark if cpu else None
    gpu_score = gpu.g3d_mark if gpu else None

    genre_tiers = cpu_genre_tiers(cpu) if cpu else None
    bracket = gpu.tpu_reco_res if gpu else None
    secondary = gpu.tpu_secondary_res if gpu else None

    res_brackets = None
    if bracket:
        res_brackets = {"primary": bracket}
        if secondary:
            res_brackets["secondary"] = f"{secondary} (playable, low end)"
        if gpu and gpu.g3d_fallback:
            res_brackets["source"] = "g3d-band"

    bottleneck = None
    tier = None
    comb = None
    if device_class in ("desktop", "laptop", "server"):
        if bracket and genre_tiers:
            tier, bottleneck = tier_for(bracket, genre_tiers, genre="aaa")
        else:
            # only one side scored — tier from what we have, flag the gap
            have_gpu = bracket is not None
            have_cpu = bool(genre_tiers)
            if have_gpu and not have_cpu:
                tier, bottleneck = _cap_to_tier(_bracket_cap(bracket)), "cpu-missing"
            elif have_cpu and not have_gpu:
                tier, bottleneck = (_cap_to_tier(_score_to_caps(genre_tiers["aaa"])),
                                    "gpu-missing")
            # neither → tier stays None (nothing to rank on)
        comb = combined_score(device_class, cpu_score, gpu_score,
                              cpu.cpu_mark if cpu else None,
                              gpu.g3d_mark if gpu else None)

    price = listing.get("price_eur")
    vscore = value_score(price, comb,
                         cpu.cpu_mark if cpu else None,
                         gpu.g3d_mark if gpu else None, device_class)
    vpe = value_per_eur(price, comb,
                        cpu.cpu_mark if cpu else None,
                        gpu.g3d_mark if gpu else None, device_class)

    canonical = cpu_model or gpu_model
    return ScoreResult(
        device_class=device_class,
        cpu_model=cpu_model, cpu_score=cpu_score,
        gpu_model=gpu_model, gpu_score=gpu_score,
        canonical_model=canonical,
        res_brackets=res_brackets, genre_tiers=genre_tiers,
        bottleneck=bottleneck, combined_score=comb, tier=tier,
        value_score=vscore, value_per_eur=vpe,
        spec_confidence=title_spec.confidence,
    )


# ---------------------------------------------------------------- DB drivers

def _primary_conn():
    for role, url in _targets():
        if role == "primary":
            conn = connect(url)
            ensure_schema(conn)
            return conn
    raise SystemExit("no primary database configured (LOCAL_DATABASE_URL)")


def fetch_listings(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT l.item_id, l.title, l.category, l.last_state,
                   l.enriched_title, l.desc_cpu, l.desc_gpu,
                   (SELECT o.price_eur FROM {SCHEMA}.observations o
                    WHERE o.item_id = l.item_id
                    ORDER BY o.observed_at DESC LIMIT 1) AS price_eur
            FROM {SCHEMA}.listings l
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def spec_rows(results: dict[str, ScoreResult]) -> list[tuple]:
    now = _now()
    rows = []
    for item_id, r in results.items():
        rows.append((
            item_id, r.device_class, r.cpu_model, r.cpu_score, r.gpu_model,
            r.gpu_score, r.canonical_model, _jsonb(r.res_brackets),
            _jsonb(r.genre_tiers), r.bottleneck, r.combined_score, r.tier,
            r.value_score, r.spec_confidence, now,
        ))
    return rows


def run_score() -> dict[str, ScoreResult]:
    conn = _primary_conn()
    try:
        listings = fetch_listings(conn)
    finally:
        conn.close()
    cpu_idx = load_cpu_index()
    gpu_idx = load_gpu_index()
    results: dict[str, ScoreResult] = {}
    for l in listings:
        results[l["item_id"]] = score_listing(l, cpu_idx, gpu_idx)
    save_listing_specs(spec_rows(results))
    return results


def calibrate() -> None:
    results = run_score()
    priced = [(r.value_score, r.combined_score, r.tier)
              for r in results.values()
              if r.value_score is not None and r.device_class in ("desktop", "laptop", "server")]
    if not priced:
        print("no priced whole-PC listings to calibrate yet — run a crawl first")
        return
    vs = sorted(v for v, _, _ in priced)
    cs = sorted(c for _, c, _ in priced if c is not None)
    def pct(arr, p):
        return arr[min(len(arr) - 1, int(len(arr) * p))]
    print(f"n={len(priced)} whole-PC listings with value_score")
    print("value_score  (€/1k pts, lower=better): "
          f"p10={pct(vs,.10)} p25={pct(vs,.25)} p50={pct(vs,.50)} p75={pct(vs,.75)}")
    if cs:
        print("combined_score (k pts): "
              f"p10={pct(cs,.10)} p25={pct(cs,.25)} p50={pct(cs,.50)} p75={pct(cs,.75)}")
    from collections import Counter
    print("tier counts:", dict(Counter(r.tier for r in results.values() if r.tier)))
    print("confidence:", dict(Counter(r.spec_confidence for r in results.values())))
    print("→ set config.json tier_thresholds from combined_score p25/p75 if skewed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true",
                    help="score + print distribution for threshold calibration")
    args = ap.parse_args()
    if args.calibrate:
        calibrate()
        return 0
    results = run_score()
    from collections import Counter
    print(f"scored {len(results)} listings")
    print("device_class:", dict(Counter(r.device_class for r in results.values())))
    print("tier:", dict(Counter(r.tier for r in results.values() if r.tier)))
    print("confidence:", dict(Counter(r.spec_confidence for r in results.values())))
    unscored = [r for r in results.values()
                if r.spec_confidence != "low" and r.cpu_score is None and r.gpu_score is None]
    print(f"unscored (no benchmark match): {len(unscored)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
