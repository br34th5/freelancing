"""Reference benchmark data: PassMark CPU/GPU scores + TechPowerUp reco cache.

Bi-monthly (60d) refresh cadence; missing models are collected as unscored
warnings — never auto-refreshed beyond the fixed cadence.

Usage:
  python3 benchmarks.py refresh [--force]
  python3 benchmarks.py --refresh <model>          # TPU reco for one model (must be in tpu_map)
  python3 benchmarks.py --add-tpu <model> <url>    # register a TPU specs URL, fetch reco
  python3 benchmarks.py --seed-verify [n]          # verify next n unverified tpu_map entries
  python3 benchmarks.py --l3 <cpu_name>            # fetch L3 for one CPU (cached)

Data files (data/):
  cpu_scores.json  model → {cpu_mark, st_mark, rank, cpu_id, l3_mb?, scraped_at}
  gpu_scores.json  model → {g3d_mark, rank, scraped_at}
  tpu_reco.json    model → {url, reco: [(res, quality)...], scraped_at}
  tpu_map.json     model → {slug, verified}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scrapling import fetchers

from specs import normalize_key

DATA = Path(__file__).parent / "data"
CONFIG = json.loads((Path(__file__).parent / "config.json").read_text())

PM_CPU_LIST = "https://www.cpubenchmark.net/cpu-list/all"
PM_ST = "https://www.cpubenchmark.net/singleThread.html"
PM_GPU_LIST = "https://www.videocardbenchmark.net/gpu_list.php"
PM_CPU_PAGE = "https://www.cpubenchmark.net/cpu.php?cpu={name}&id={id}"

TPU_BASE = "https://www.techpowerup.com/gpu-specs/"

# Seed map: common skelbiu-market GPUs. verified=true entries also carry
# P0-confirmed reco (no refetch needed). Unverified ids get checked by
# --seed-verify slowly (TPU rate-limits aggressively).
TPU_SEED = {
    "GeForce RTX 4090": {"slug": "geforce-rtx-4090.c4182", "verified": True},
    "GeForce RTX 4070 Ti SUPER": {"slug": "geforce-rtx-4070-ti-super.c4187", "verified": True},
    "GeForce GTX 1650": {"slug": "geforce-gtx-1650.c1476", "verified": True},
    "Radeon RX 6600 XT": {"slug": "radeon-rx-6600-xt.c3730", "verified": True},
    "GeForce RTX 4080 SUPER": {"slug": "geforce-rtx-4080-super.c4181", "verified": False},
    "GeForce RTX 4070 SUPER": {"slug": "geforce-rtx-4070-super.c4185", "verified": False},
    "GeForce RTX 4070 Ti": {"slug": "geforce-rtx-4070-ti.c4186", "verified": False},
    "GeForce RTX 4070": {"slug": "geforce-rtx-4070.c4183", "verified": False},
    "GeForce RTX 4060 Ti": {"slug": "geforce-rtx-4060-ti.c4178", "verified": False},
    "GeForce RTX 4060": {"slug": "geforce-rtx-4060.c4177", "verified": False},
    "GeForce RTX 3090": {"slug": "geforce-rtx-3090.c3687", "verified": False},
    "GeForce RTX 3080": {"slug": "geforce-rtx-3080.c3688", "verified": False},
    "GeForce RTX 3070": {"slug": "geforce-rtx-3070.c3691", "verified": False},
    "GeForce RTX 3060": {"slug": "geforce-rtx-3060.c3694", "verified": False},
    "GeForce RTX 2080 Ti": {"slug": "geforce-rtx-2080-ti.c3748", "verified": False},
    "GeForce RTX 2070": {"slug": "geforce-rtx-2070.c3751", "verified": False},
    "GeForce RTX 2060": {"slug": "geforce-rtx-2060.c3752", "verified": False},
    "GeForce GTX 1660 SUPER": {"slug": "geforce-gtx-1660-super.c3736", "verified": False},
    "GeForce GTX 1660": {"slug": "geforce-gtx-1660.c3742", "verified": False},
    "GeForce GTX 1650 SUPER": {"slug": "geforce-gtx-1650-super.c3756", "verified": False},
    "GeForce GTX 1080 Ti": {"slug": "geforce-gtx-1080-ti.c3604", "verified": False},
    "GeForce GTX 1080": {"slug": "geforce-gtx-1080.c3603", "verified": False},
    "GeForce GTX 1070": {"slug": "geforce-gtx-1070.c3605", "verified": False},
    "GeForce GTX 1060 6GB": {"slug": "geforce-gtx-1060-6gb.c3607", "verified": False},
    "GeForce GTX 1050 Ti": {"slug": "geforce-gtx-1050-ti.c3609", "verified": False},
    "Radeon RX 7900 XTX": {"slug": "radeon-rx-7900-xtx.c4015", "verified": False},
    "Radeon RX 7900 XT": {"slug": "radeon-rx-7900-xt.c4016", "verified": False},
    "Radeon RX 7800 XT": {"slug": "radeon-rx-7800-xt.c4024", "verified": False},
    "Radeon RX 7700 XT": {"slug": "radeon-rx-7700-xt.c4023", "verified": False},
    "Radeon RX 7600": {"slug": "radeon-rx-7600.c4019", "verified": False},
    "Radeon RX 6950 XT": {"slug": "radeon-rx-6950-xt.c3741", "verified": False},
    "Radeon RX 6800 XT": {"slug": "radeon-rx-6800-xt.c3738", "verified": False},
    "Radeon RX 6700 XT": {"slug": "radeon-rx-6700-xt.c3739", "verified": False},
    "Radeon RX 6600": {"slug": "radeon-rx-6600.c3745", "verified": False},
    "Radeon RX 580": {"slug": "radeon-rx-580.c3039", "verified": False},
    "Radeon RX 5700 XT": {"slug": "radeon-rx-5700-xt.c3477", "verified": False},
    "Arc A770": {"slug": "arc-a770.c3919", "verified": False},
    "Arc A750": {"slug": "arc-a750.c3918", "verified": False},
}

# P0-confirmed reco (skip refetch)
TPU_SEED_RECO = {
    "GeForce RTX 4090": [("1920x1080", "Good"), ("2560x1440", "Good"), ("3840x2160", "Good")],
    "GeForce RTX 4070 Ti SUPER": [("1920x1080", "Good"), ("2560x1440", "Good"), ("3840x2160", "Good")],
    "GeForce GTX 1650": [("640x480", "Playable"), ("1280x720", "Unplayable"), ("1366x768", "Unplayable")],
    "Radeon RX 6600 XT": [("640x480", "Good"), ("1280x720", "Low FPS"), ("1366x768", "Unplayable")],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(name: str) -> dict:
    path = DATA / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save(name: str, obj: dict) -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / f"{name}.json").write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def _fresh(entry: dict, max_age_days: int) -> bool:
    ts = entry.get("scraped_at")
    if not ts:
        return False
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - t < timedelta(days=max_age_days)


# ---------------------------------------------------------------- PassMark

def _pm_get(url: str):
    delay = CONFIG.get("passmark_delay_seconds", 4)
    resp = fetchers.Fetcher.get(url, timeout=40)
    if resp.status != 200:
        raise RuntimeError(f"PassMark {url} -> {resp.status}")
    time.sleep(delay)
    return resp


def fetch_passmark_cpus() -> dict:
    from scrapling.parser import Selector

    print("fetching PassMark CPU list…")
    resp = _pm_get(PM_CPU_LIST)
    page = Selector(resp.html_content)
    out: dict = {}
    for row in page.css("tr[id^=cpu]"):
        tds = row.css("td")
        if len(tds) < 3:
            continue
        link = tds[0].css("a")
        if not link:
            continue
        name = link[0].text.strip()
        if not name:
            continue
        mark = tds[1].text.strip().replace(",", "")
        rank = tds[2].text.strip().replace(",", "")
        m_id = re.search(r"[?&]id=(\d+)", link[0].attrib.get("href", ""))
        try:
            out[name] = {
                "cpu_mark": int(mark),
                "rank": int(rank),
                "cpu_id": int(m_id.group(1)) if m_id else None,
            }
        except ValueError:
            continue
    print(f"  {len(out)} CPUs")
    return out


def fetch_passmark_st() -> dict:
    from scrapling.parser import Selector

    print("fetching PassMark single-thread…")
    resp = _pm_get(PM_ST)
    page = Selector(resp.html_content)
    names = page.css("span.prdname")
    counts = page.css("span.count")
    out: dict = {}
    for n, c in zip(names, counts):
        name = n.text.strip()
        val = c.text.strip().replace(",", "")
        if not name or not val:
            continue
        try:
            out[name] = int(val)
        except ValueError:
            continue
    print(f"  {len(out)} ST entries")
    return out


def fetch_passmark_gpus() -> dict:
    from scrapling.parser import Selector

    print("fetching PassMark GPU list…")
    resp = _pm_get(PM_GPU_LIST)
    page = Selector(resp.html_content)
    out: dict = {}
    for row in page.css("tr[id^=gpu]"):
        tds = row.css("td")
        if len(tds) < 3:
            continue
        link = tds[0].css("a")
        name = (link[0].text.strip() if link else tds[0].text.strip())
        if not name:
            continue
        g3d = tds[1].text.strip().replace(",", "")
        rank = tds[2].text.strip().replace(",", "")
        try:
            out[name] = {"g3d_mark": int(g3d), "rank": int(rank), "scraped_at": _now()}
        except ValueError:
            continue
    print(f"  {len(out)} GPUs")
    return out


def join_cpu_scores(cpus: dict, st: dict) -> dict:
    """Merge MT list + ST list on normalized names; carry over existing l3.
    TDP comes from the static data/cpu_tdp.json lookup so the NAS-efficiency
    report can rank by wattage even when PassMark doesn't surface TDP.
    """
    existing = _load("cpu_scores")
    st_by_key = {normalize_key(k): v for k, v in st.items()}
    clock_suffix = re.compile(r"(\d+(?:\.\d+)?)(?:mhz|ghz|hz)")
    tdp_static = _load("cpu_tdp")

    def _clock_variant(cpu_key: str, st_key: str) -> bool:
        longer, shorter = (st_key, cpu_key) if len(st_key) > len(cpu_key) else (cpu_key, st_key)
        if not longer.startswith(shorter):
            return False
        rest = longer[len(shorter):]
        return bool(clock_suffix.fullmatch(rest))

    out: dict = {}
    unmatched_st = set(st_by_key)
    for name, info in cpus.items():
        key = normalize_key(name)
        st_val = st_by_key.get(key)
        if st_val is not None:
            unmatched_st.discard(key)
        else:
            for k2, v2 in st_by_key.items():
                if _clock_variant(key, k2):
                    st_val = v2
                    unmatched_st.discard(k2)
                    break
        prev = existing.get(name, {})
        out[name] = {
            "cpu_mark": info["cpu_mark"],
            "st_mark": st_val,
            "rank": info["rank"],
            "cpu_id": info.get("cpu_id"),
            "l3_mb": prev.get("l3_mb"),
            # Preserve the TDP across refreshes — prefer the static lookup
            # so a stale PassMark page (no TDP field) doesn't lose it.
            "tdp_w": prev.get("tdp_w") or tdp_static.get(name),
            "scraped_at": _now(),
        }
    print(f"joined: {sum(1 for v in out.values() if v['st_mark'])}/{len(out)} with ST, "
          f"{len(unmatched_st)} ST-only entries ignored, "
          f"{sum(1 for v in out.values() if v.get('tdp_w'))}/{len(out)} with TDP")
    return out


# ---------------------------------------------------------------- TPU

def _tpu_title_ok(model: str, page_title: str) -> bool:
    """Guard against TPU soft-block serving wrong GPU pages (seen 2026-10-11)."""
    mt = normalize_key(model)
    pt = normalize_key(page_title)
    # every alnum chunk of the model (e.g. 4070, ti, super) must appear in title
    chunks = re.findall(r"[a-z]+|\d+", mt)
    return all(c in pt for c in chunks if len(c) >= 2 or c.isdigit())


def _tpu_get(url: str, model: str):
    delay = CONFIG.get("tpu_delay_seconds", 12)
    resp = fetchers.Fetcher.get(url, impersonate=CONFIG.get("fetch_impersonate", "chrome131"), timeout=30)
    time.sleep(delay)
    from scrapling.parser import Selector

    page = Selector(resp.html_content)
    title_el = page.css("title")
    title = title_el[0].text.strip() if title_el else ""
    if "bot check" in title.lower():
        raise RuntimeError("TPU bot-check — abort run, retry later")
    if resp.status != 200:
        raise RuntimeError(f"TPU {url} -> {resp.status}")
    if not _tpu_title_ok(model, title):
        raise RuntimeError(f"TPU title mismatch (soft-block?): want {model!r} got {title!r}")
    return page


def fetch_tpu_reco(model: str, url: str) -> list:
    page = _tpu_get(url, model)
    entries = [
        (el.text.strip(), el.attrib.get("title", ""))
        for el in page.css(".gpudb-recommended-resolutions__entry")
    ]
    if not entries:
        raise RuntimeError(f"no reco block on {url} for {model}")
    return entries


def ensure_tpu_map() -> dict:
    m = _load("tpu_map")
    if not m:
        m = {k: dict(v) for k, v in TPU_SEED.items()}
        _save("tpu_map", m)
    return m


def seed_tpu_reco_cache() -> None:
    """Write P0-confirmed reco into cache without network."""
    reco = _load("tpu_reco")
    changed = False
    for model, entries in TPU_SEED_RECO.items():
        if model not in reco:
            tmap = ensure_tpu_map()
            slug = tmap.get(model, {}).get("slug", "")
            reco[model] = {
                "url": TPU_BASE + slug,
                "reco": entries,
                "scraped_at": _now(),
            }
            changed = True
    if changed:
        _save("tpu_reco", reco)


# ---------------------------------------------------------------- L3

def fetch_l3(model: str) -> float | None:
    cpus = _load("cpu_scores")
    entry = cpus.get(model)
    if not entry or not entry.get("cpu_id"):
        print(f"no cpu_id for {model!r} — run refresh first")
        return None
    url = PM_CPU_PAGE.format(name=model.replace(" ", "+"), id=entry["cpu_id"])
    resp = _pm_get(url)
    m = re.search(r"L3 Cache</[^>]+>\s*<[^>]+>([\d.,]+)\s*MB", resp.html_content, re.I)
    if not m:
        m = re.search(r"L3 Cache.{0,80}?([\d.,]+)\s*MB", resp.html_content, re.I | re.S)
    if not m:
        print(f"L3 not found on page for {model}")
        return None
    val = float(m.group(1).replace(",", ""))
    entry["l3_mb"] = val
    cpus[model] = entry
    _save("cpu_scores", cpus)
    print(f"{model}: L3 = {val} MB")
    return val


# ---------------------------------------------------------------- CLI ops

def cmd_refresh(force: bool = False) -> None:
    age = CONFIG.get("refresh_interval_days", 60)
    cpus_prev = _load("cpu_scores")
    if force or not cpus_prev or not any(_fresh(v, age) for v in cpus_prev.values()):
        cpus = join_cpu_scores(fetch_passmark_cpus(), fetch_passmark_st())
        _save("cpu_scores", cpus)
    else:
        print("cpu_scores fresh — skip (use --force)")

    gpus_path = DATA / "gpu_scores.json"
    gpus_prev = _load("gpu_scores")
    any_fresh = gpus_prev and any(_fresh(v, age) for v in gpus_prev.values())
    if force or not any_fresh:
        _save("gpu_scores", fetch_passmark_gpus())
    else:
        print("gpu_scores fresh — skip (use --force)")

    ensure_tpu_map()
    seed_tpu_reco_cache()
    print("done.")


def cmd_tpu_refresh(model: str, url: str | None = None) -> None:
    tmap = ensure_tpu_map()
    if url:
        slug = url.replace(TPU_BASE, "")
        tmap[model] = {"slug": slug, "verified": True}
        _save("tpu_map", tmap)
    elif model in tmap:
        slug = tmap[model]["slug"]
    else:
        print(f"{model!r} not in tpu_map — pass URL: --add-tpu {model!r} <tpu-url>")
        sys.exit(2)

    entries = fetch_tpu_reco(model, TPU_BASE + slug)
    reco = _load("tpu_reco")
    reco[model] = {"url": TPU_BASE + slug, "reco": entries, "scraped_at": _now()}
    _save("tpu_reco", reco)
    tmap[model]["verified"] = True
    _save("tpu_map", tmap)
    print(f"{model}: {entries}")


def cmd_seed_verify(n: int = 3) -> None:
    tmap = ensure_tpu_map()
    reco = _load("tpu_reco")
    done = 0
    for model, info in tmap.items():
        if done >= n:
            break
        if info.get("verified") or model in reco:
            continue
        try:
            entries = fetch_tpu_reco(model, TPU_BASE + info["slug"])
            reco[model] = {"url": TPU_BASE + info["slug"], "reco": entries, "scraped_at": _now()}
            info["verified"] = True
            _save("tpu_reco", reco)
            _save("tpu_map", tmap)
            print(f"verified {model}: {entries}")
            done += 1
        except RuntimeError as e:
            print(f"abort: {e} (stop verifying to respect TPU limits)")
            break
    print(f"verified {done} model(s)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", choices=["refresh"], default="refresh")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh", metavar="MODEL", help="TPU reco for one model")
    ap.add_argument("--add-tpu", nargs=2, metavar=("MODEL", "URL"))
    ap.add_argument("--seed-verify", nargs="?", const=3, type=int, metavar="N")
    ap.add_argument("--l3", metavar="CPU_NAME")
    args = ap.parse_args()

    if args.refresh:
        cmd_tpu_refresh(args.refresh)
    elif args.add_tpu:
        cmd_tpu_refresh(args.add_tpu[0], args.add_tpu[1])
    elif args.seed_verify is not None:
        cmd_seed_verify(args.seed_verify)
    elif args.l3:
        fetch_l3(args.l3)
    else:
        cmd_refresh(force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
