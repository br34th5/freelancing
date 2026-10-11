"""Fixtures + tests for specs.py. Run: python3 test_specs.py"""

from specs import analyze_title, extract_cpus, extract_gpus, normalize_key

FIXTURES = [
    # (title, expect_cpus, expect_gpus, expect_confidence, expect_low_flags)
    (
        "Lenovo Legion Pro 7 i9 4090 32GB 2TB",
        [],  # "i9" without sku → no CPU model; 4090 → GPU via bare fallback
        ["GeForce RTX 4090"],
        "high",
        False,
    ),
    (
        "Zaidimu kompiuteris Ryzen 7 7800X3D RTX 4070 Ti 32GB",
        ["AMD Ryzen 7 7800X3D"],
        ["GeForce RTX 4070 Ti"],
        "high",
        False,
    ),
    (
        "Nvidia Geforce Rtx 4060 8gb Gamerock",
        [],
        ["GeForce RTX 4060"],
        "high",
        False,
    ),
    (
        "Gtx 1080 8gb gamerock",
        [],
        ["GeForce GTX 1080"],
        "high",
        False,
    ),
    (
        "Procesorius AMD Ryzen 5 5600 AM4",
        ["AMD Ryzen 5 5600"],
        [],
        "high",
        False,
    ),
    (
        "Intel Core i7-12700H nešiojamam",
        ["Intel Core i7-12700H"],
        [],
        "med",
        False,
    ),
    (
        "Superku Nvidia Geforce Rtx 4090",
        [],
        ["GeForce RTX 4090"],
        "low",
        True,
    ),
    (
        "12vnt intel Cpu G4400 15€",
        [],
        [],
        "low",
        True,
    ),
    (
        "i5-3470+cooler+mainboard+8GB 30€",
        ["Intel Core i5-3470"],
        [],
        "low",
        True,
    ),
    (
        "Riser cable PCIe 4.0 x16",
        [],
        [],
        "low",
        True,
    ),
    (
        "Mining rig 6x RX 580 8GB",
        [],
        ["Radeon RX 580"],
        "low",
        True,
    ),
    (
        "Dell PowerEdge R740 Xeon Gold 6248 32GB",
        ["Intel Xeon Gold 6248"],
        [],
        "high",
        False,
    ),
    (
        "RTX 3070 broken neveikia defektas",
        [],
        ["GeForce RTX 3070"],
        "low",
        True,
    ),
    (
        "HP ZBook Fury G8 i7-11800H RTX A2000",
        ["Intel Core i7-11800H"],
        [],  # RTX A-series (Quadro naming) not matched — acceptable miss
        "high",
        False,
    ),
    (
        "Ieško rtx 4070",
        [],
        ["GeForce RTX 4070"],
        "low",
        True,
    ),
]


def _match_prefix(name: str, expected: list[str]) -> bool:
    if not expected:
        return not name
    return any(name.startswith(e) or e in name for e in expected)


def run() -> int:
    failures = 0
    for i, (title, exp_cpus, exp_gpus, exp_conf, exp_low) in enumerate(FIXTURES, 1):
        spec = analyze_title(title)
        ok = True
        notes = []

        if exp_low:
            if not (spec.is_ad or spec.is_junk or spec.confidence == "low"):
                ok = False
                notes.append("expected low/ad/junk")
        else:
            if spec.is_ad or spec.is_junk:
                ok = False
                notes.append(f"unexpected low flags ad={spec.is_ad} junk={spec.is_junk}")
            if spec.confidence != exp_conf:
                ok = False
                notes.append(f"conf {spec.confidence}!={exp_conf}")
            for e in exp_cpus:
                if not any(e in c for c in spec.cpus):
                    ok = False
                    notes.append(f"missing cpu {e} in {spec.cpus}")
            for e in exp_gpus:
                if not any(e in g for g in spec.gpus):
                    ok = False
                    notes.append(f"missing gpu {e} in {spec.gpus}")

        status = "ok " if ok else "FAIL"
        print(f"[{status}] {i:2}. {title[:58]!r}")
        print(f"       cpus={spec.cpus} gpus={spec.gpus} conf={spec.confidence} "
              f"ad={spec.is_ad} junk={spec.is_junk} reasons={spec.reasons}")
        if notes:
            print(f"       !! {'; '.join(notes)}")
            failures += 1
    print(f"\n{len(FIXTURES) - failures}/{len(FIXTURES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
