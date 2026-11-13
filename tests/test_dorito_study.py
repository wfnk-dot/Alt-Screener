import random
from datetime import datetime, timezone

from screener.dorito_study import aggregate, outcome, report, simulate, study_coin, wilson

T0 = int(datetime(2023, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)  # a Monday


def fake_15m(n=96 * 400, seed=3):
    rnd = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        o = p
        p *= 1 + rnd.gauss(0, 0.003) + 0.0008 * ((i // 3000) % 2 * 2 - 1)
        out.append((T0 + i * 900_000, o, max(o, p) * (1 + abs(rnd.gauss(0, 0.001))), min(o, p) * (1 - abs(rnd.gauss(0, 0.001))), p))
    return out


def test_aggregate_alignment():
    bars = fake_15m(96 * 21)
    d = aggregate(bars, "1d")
    assert len(d) == 21 and d[0].start == T0 and d[0].open == bars[0][1] and d[0].close == bars[95][4]
    assert d[0].high == max(b[2] for b in bars[:96]) and d[0].last == 95
    w = aggregate(bars, "1w")
    assert len(w) == 3 and w[0].start == T0  # weeks start Monday
    three = aggregate(bars, "3d")
    assert all((c.start // 86_400_000) % 3 == 0 for c in three)


def test_simulate_long_and_short():
    bars = [(0, 10, 10, 10, 10), (1, 10, 10.5, 9.95, 10.4), (2, 10.4, 12.2, 10.3, 12), (3, 12, 12, 8, 9)]
    risk, best, stopped, final = simulate(bars, 1, 10.0, 9.0, 1, 10)
    assert stopped and abs(best - 2.2) < 1e-9 and final == -1.0
    t = {"risk": risk, "best": best, "stopped": stopped, "final": final}
    assert outcome(t, 2) == 2 - 0.10 / risk and outcome(t, 3) == -1 - 0.10 / risk
    _, best, stopped, final = simulate(bars, 1, 10.0, 10.6, -1, 1)
    assert not stopped and abs(best - (10 - 9.95) / 0.6) < 1e-9 and abs(final - (10 - 10.4) / 0.6) < 1e-9
    _, _, stopped, _ = simulate(bars, 1, 10.0, 10.6, -1, 2)  # next bar's high 12.2 hits the short's stop
    assert stopped


def test_wilson():
    lo, hi = wilson(50, 100)
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60


def test_study_runs_end_to_end():
    lv, tr = study_coin(fake_15m(), "Big alts", T0 + 300 * 86_400_000, random.Random(1))
    assert lv and any(t["kind"] == "dorito" for t in tr) and any(t["kind"] == "control" for t in tr)
    text = report(lv, tr, 1, 1.1, "x")
    assert "Setups that pass every check" in text and "Does the level hold?" in text
