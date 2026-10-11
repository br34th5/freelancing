"""Offline tests for spider.py proxy health (CF-sticky rotator strategy).
Run: python3 test_spider_health.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from spider import ProxyHealth, CHAMP_FAIL_LIMIT

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"[{'ok ' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")


def run():
    P = ["http://a:1", "http://b:2", "http://c:3"]

    # --- probation cycles candidates until one reports ok
    h = ProxyHealth(P)
    check("probation first", h.strategy(P, 0)[0], P[0])
    h.report_blocked()
    check("probation second", h.strategy(P, 0)[0], P[1])
    h.report_ok()
    check("champion crowned", h.champion, 1)
    check("champion sticky", h.strategy(P, 0)[0], P[1])
    h.report_ok()
    check("champion sticky again", h.strategy(P, 0)[0], P[1])
    h.report_ok()

    # --- champion benched after CHAMP_FAIL_LIMIT consecutive blocks
    for _ in range(CHAMP_FAIL_LIMIT):
        check("champion serves", h.strategy(P, 0)[0], P[1])
        h.report_blocked()
    check("champion benched", h.champion, None)
    check("next candidate promoted", h.strategy(P, 0)[0], P[2])

    # --- unreported (errored) request counts as a fail on next issue
    h2 = ProxyHealth(P)
    h2.strategy(P, 0)          # issued, never classified
    h2.strategy(P, 0)          # next issue → previous counted failed
    check("unreported counts as fail", h2.fails[0], 1)

    # --- whole-pool bench → selection must keep advancing, never wedge.
    # 2 proxies x CHAMP_FAIL_LIMIT fails exhausts the pool; the next issue
    # resets fails and keeps serving rather than stalling on a dead pool.
    Q = ["http://x:1", "http://y:2"]
    h3 = ProxyHealth(Q)
    for _ in range(2 * CHAMP_FAIL_LIMIT):
        h3.strategy(Q, 0)
        h3.report_blocked()
    check("pool exhausted", all(f >= CHAMP_FAIL_LIMIT for f in h3.fails), True)
    served = h3.strategy(Q, 0)[0]      # must still hand out a proxy
    check("pool keeps serving after exhaustion", served in Q, True)
    check("reset happened", any(f == 0 for f in h3.fails), True)

    # --- report idempotency (double is_blocked call must not double-count)
    h4 = ProxyHealth(["http://z:1"])
    R = ["http://z:1"]
    h4.strategy(R, 0)
    h4.report_blocked()
    h4.report_blocked()        # second call for same response — ignored
    check("report_blocked idempotent", h4.fails[0], 1)

    # --- snapshot never leaks credentials
    h5 = ProxyHealth(["http://user:pass@host:99"])
    h5.champion = 0
    snap = h5.snapshot()
    check("snapshot redacts creds", "user:pass@" not in snap and "***@" in snap, True)

    print(f"\n{'PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run())
