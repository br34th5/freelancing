"""Title → CPU/GPU model extraction, canonicalization, lot/junk/ad detection.

Pure logic, no network. Used by spider extraction (P2) and scoring (P4).

Confidence:
  high — one clean component/PC model, no junk signals
  med  — model found but noisy (mobile SKU, extra hardware words, mixed parts)
  low  — lot/bundle/broken/wanted-ad → excluded from comparables
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


@dataclass
class TitleSpec:
    cpus: list[str] = field(default_factory=list)
    gpus: list[str] = field(default_factory=list)
    confidence: str = "low"
    is_ad: bool = False
    is_junk: bool = False
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- patterns

_CPU_INTEL = re.compile(
    r"\b(?:intel\s+)?core\s+i([3579])[-\s]?(\d{4,5})([KHFSU]E?)?\b"
    r"|\bi([3579])[-\s]?(\d{4,5})([KHFSU]E?)?\b",
    re.I,
)
# Order matters: the explicit X3D branch runs before the loose \d{3,4} branch
# so that "Ryzen 7 9800X3D" / "Ryzen 7 5800X3D" pick up the correct tier
# instead of falling through to the X3D fallback's first-digit heuristic
# (which would wrongly tag "5800X3D" as Ryzen 5).
_CPU_AMD = re.compile(
    r"\b(?:amd\s+)?ryzen\s+(pro\s+)?([3579])\s+(\d{4})X3D\b"
    r"|\b(?:amd\s+)?ryzen\s+([3579])\s+(\d{4})([XHUS]?)?\b"
    r"|\bryzen\s+([3579])(\d{4})([XHUS]?)?\b"
    r"|\b(?:amd\s+)?ryzen\s+(pro\s+)?([3579])\s+(\d{3,4})([XH]?)?\b",
    re.I,
)
_CPU_AMD_X3D = re.compile(r"\b(\d{4})X3D\b", re.I)
_CPU_XEON = re.compile(r"\bxeon\s+((?:gold|silver|bronze|platinum)\s+|e[35]-?)?([a-z]?\d{4,5}[vA-Z]?\d?)\b", re.I)
_CPU_EPIC = re.compile(r"\bepyc\s+(\d{4,5}[a-z]?)\b", re.I)
_CPU_OTHER = re.compile(r"\b(pentium|celeron|athlon)\s*([a-z]?\d{3,5}[a-z]?)?\b", re.I)

_GPU_NVIDIA = re.compile(
    r"\b(?:nvidia\s+)?(rtx|gtx|gt)\s*(\d{3,4})\s*(ti|super)?\s*(ti|super)?\b",
    re.I,
)
# bare model numbers in PC titles ("Legion i9 4090") — known GPU skus only
_GPU_BARE = re.compile(r"\b(?:r?tx\s*)?(\d{4})\s*(ti|super)?\b", re.I)
_BARE_GPU_SKUS = {
    "1050", "1060", "1070", "1080",
    "1630", "1650", "1660",
    "2060", "2070", "2080",
    "3050", "3060", "3070", "3080", "3090",
    "4060", "4070", "4080", "4090",
    "5060", "5070", "5080", "5090",
}
_GPU_AMD = re.compile(
    r"\b(?:radeon\s+)?rx\s*(\d{3,4})\s*(xtx|xt|gre)?\b"
    r"|\bradeon\s+r([57])\s*(\d{3,4})\b",
    re.I,
)
_GPU_INTEL = re.compile(r"\b(?:intel\s+)?arc\s+a\s?(\d{3})\b", re.I)
_GPU_APPLE = re.compile(r"\bapple\s+(m[123]|a\d+)\s*(pro|max|ultra)?\b", re.I)

# whole-PC / laptop hints
_DEVICE_CLASS = re.compile(
    r"\b(laptop|notebook|nesiojam\w*|zaidimu[\s-]?kompiuter\w*|stacionarus|serveris|server|kompiuteris)\b",
    re.I,
)


def _clean(text: str) -> str:
    """Unicode-normalize, strip Lithuanian diacritics, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text or "")
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", stripped).strip()


def _join_gpu(group: tuple, vendor: str) -> str:
    parts = [p for p in group if p]
    if vendor == "nvidia":
        core, num = parts[0].upper(), parts[1]
        extras = [p.upper() for p in parts[2:] if p]
        extras.sort(key=lambda x: 0 if x == "TI" else 1)
        pretty = {"TI": "Ti", "SUPER": "SUPER"}
        return " ".join(["GeForce", core, num] + [pretty.get(e, e) for e in extras])
    if vendor == "amd_rx":
        num = parts[0]
        extra = parts[1].upper() if len(parts) > 1 and parts[1] else ""
        return " ".join(["Radeon", "RX", num] + ([extra] if extra else []))
    if vendor == "amd_r":
        series, num = parts[0], parts[1]
        return f"Radeon R{series} {num}"
    if vendor == "intel":
        return f"Arc A{parts[0]}"
    if vendor == "apple":
        name = f"Apple {parts[0].upper()}"
        if len(parts) > 1 and parts[1]:
            name += f" {parts[1].capitalize()}"
        return name
    return ""


def extract_cpus(title: str) -> list[str]:
    t = _clean(title)
    found: list[str] = []
    x3d_nums = {m.group(1) for m in _CPU_AMD_X3D.finditer(t)}

    for m in _CPU_INTEL.finditer(t):
        tier = m.group(1) or m.group(4)
        num = m.group(2) or m.group(5)
        suffix = (m.group(3) or m.group(6) or "").upper()
        # i9 skus start at 9900 / 10xxx — reject "i9 4090" (4090 = GPU)
        if tier == "9" and len(num) == 4 and int(num) < 9900:
            continue
        found.append(f"Intel Core i{tier}-{num}{suffix}")

    for m in _CPU_AMD.finditer(t):
        # Branch layout: (X3D-with-tier, bare 4-digit, bare no-space, 3-4 digit)
        # Group 1 = pro prefix (X3D branch only), 2/3 = tier/num; the other
        # branches slot tier/num into groups 4-9.
        if m.group(2):                   # X3D branch: m[2]=tier, m[3]=num
            tier, num = m.group(2), m.group(3)
            suf = "X3D"
        elif m.group(4):                 # bare 4-digit with space: m[4]=tier, m[5]=num
            tier, num, suf = m.group(4), m.group(5), (m.group(6) or "")
        elif m.group(7):                 # bare no-space: m[7]=tier, m[8]=num
            tier, num, suf = m.group(7), m.group(8), (m.group(9) or "")
        else:                            # 3-4 digit fallback: m[10]=pro, m[11]=tier, m[12]=num
            tier, num, suf = m.group(11), m.group(12), (m.group(13) or "")
        name = f"AMD Ryzen {tier} {num}{suf.upper()}"
        if num in x3d_nums and "X3D" not in name.upper():
            name = f"{name}X3D"
        found.append(name)

    for m in _CPU_AMD_X3D.finditer(t):
        num = m.group(1)
        if not any(num in c for c in found):
            tier_guess = "7" if num[0] == "7" else "9" if num[0] == "9" else "5"
            found.append(f"AMD Ryzen {tier_guess} {num}X3D")

    for m in _CPU_XEON.finditer(t):
        series = (m.group(1) or "").strip()
        num = m.group(2).upper()
        name = f"Intel Xeon {series} {num}".replace("  ", " ").strip()
        found.append(re.sub(r"-\s+", "-", name))
    for m in _CPU_EPIC.finditer(t):
        found.append(f"AMD EPYC {m.group(1).upper()}")
    for m in _CPU_OTHER.finditer(t):
        brand = m.group(1).capitalize()
        num = (m.group(2) or "").upper()
        if brand == "Athlon":
            found.append(f"AMD Athlon {num}".strip())
        else:
            found.append(f"Intel {brand} {num}".strip())

    seen: set[str] = set()
    out = []
    for c in found:
        c = re.sub(r"\s+", " ", c).strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def extract_gpus(title: str) -> list[str]:
    t = _clean(title)
    found: list[str] = []

    for m in _GPU_NVIDIA.finditer(t):
        found.append(_join_gpu(m.groups(), "nvidia"))
    for m in _GPU_AMD.finditer(t):
        if m.group(1):
            found.append(_join_gpu((m.group(1), m.group(2)), "amd_rx"))
        elif m.group(3):
            found.append(_join_gpu((m.group(3), m.group(4)), "amd_r"))
    for m in _GPU_INTEL.finditer(t):
        found.append(_join_gpu(m.groups(), "intel"))
    for m in _GPU_APPLE.finditer(t):
        found.append(_join_gpu(m.groups(), "apple"))

    # bare sku fallback ("Legion i9 4090") when no prefixed GPU matched
    # guard: require the number NOT to follow a non-GPU word (e.g. "Optiplex 5080")
    if not found:
        for m in _GPU_BARE.finditer(t):
            num = m.group(1)
            if num not in _BARE_GPU_SKUS:
                continue
            # reject if preceded by a word that isn't a GPU/CPU hint
            prefix_start = max(0, m.start() - 20)
            prefix = t[prefix_start:m.start()].strip()
            if prefix and re.search(r"(optiplex|thinkpad|zenbook|latitude|inspiron|vostro|precision|surface)\s*$", prefix, re.I):
                continue
            extra = (m.group(2) or "").upper()
            pretty_extra = " Ti" if extra == "TI" else " SUPER" if extra == "SUPER" else ""
            if num.startswith(("10", "16", "9")):
                found.append(f"GeForce GTX {num}{pretty_extra}")
            else:
                found.append(f"GeForce RTX {num}{pretty_extra}")

    seen: set[str] = set()
    out = []
    for g in found:
        if g and g not in seen:
            seen.add(g)
            out.append(g)
    return out


def analyze_title(title: str, config: dict | None = None) -> TitleSpec:
    if config is None:
        config = json.loads(CONFIG_PATH.read_text())

    raw = title or ""
    t = _clean(raw).lower()
    spec = TitleSpec()

    # wanted-ad guard
    guards = config.get("ad_keyword_guard", [])
    for kw in guards:
        if re.search(rf"\b{re.escape(_clean(kw).lower())}\b", t):
            spec.is_ad = True
            spec.reasons.append(f"ad-keyword:{kw}")
            break

    # lot / bundle / junk
    for pat in config.get("junk_patterns", []):
        if re.search(pat, t, re.I):
            spec.is_junk = True
            spec.reasons.append(f"junk:{pat}")
            break

    spec.cpus = extract_cpus(raw)
    spec.gpus = extract_gpus(raw)

    # multiple distinct component models in one title → likely lot/bundle
    n_models = len({*spec.cpus, *spec.gpus})
    is_device = bool(_DEVICE_CLASS.search(t))
    if n_models >= 3:
        spec.is_junk = True
        spec.reasons.append("multi-model")
    elif n_models == 2 and not is_device:
        # cpu+gpu pair outside a PC listing → bundle
        spec.reasons.append("mixed-parts")

    if spec.is_ad or spec.is_junk:
        spec.confidence = "low"
        return spec

    if n_models == 0:
        spec.confidence = "low"
        spec.reasons.append("no-model")
        return spec

    # mobile / max-q / embedded SKUs are med (different clocks than desktop)
    noisy = re.search(r"\b(max-?q|mobile|nesiojam\w*|notebook|laptop|embedded)\b", t)
    if noisy:
        spec.confidence = "med"
        spec.reasons.append(f"noisy-sku:{noisy.group(1)}")
    elif n_models >= 2 and not _DEVICE_CLASS.search(t):
        spec.confidence = "med"
    else:
        spec.confidence = "high"

    return spec


def normalize_key(name: str) -> str:
    """Aggressive normalization for PassMark / TPU fuzzy name matching."""
    s = _clean(name).lower()
    s = s.replace("geforce", "").replace("radeon", "").replace("nvidia", "")
    s = re.sub(r"\b(intel|amd|core|processor|cpu|gpu|graphics|videocard)\b", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s
