import random
from datetime import datetime, timedelta, timezone

from screener.alerts import leledc
from screener.data import Bar
from screener.dorito_stats import TF_HOURS, build_candles, replay, report, run_length


def fake_hourly(hours=24 * 400, seed=1):
    rnd = random.Random(seed)
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    price, out = 100.0, []
    for h in range(hours):
        o = price
        price *= 1 + rnd.gauss(0, 0.012) + 0.004 * ((h // 500) % 2 * 2 - 1)  # trending legs so Doritos fire
        hi, lo = max(o, price) * (1 + abs(rnd.gauss(0, 0.004))), min(o, price) * (1 - abs(rnd.gauss(0, 0.004)))
        out.append(Bar(t0 + timedelta(hours=h), o, hi, lo, price, 1.0))
    return out


def test_candles_from_hours():
    hourly = fake_hourly(24 * 10)
    days = build_candles(hourly, "1d")
    assert len(days) == 10 and all(len(c.partials) == 24 for c in days)
    d = days[3]
    hrs = hourly[72:96]
    assert (d.open, d.high, d.low, d.close) == (hrs[0].open, max(b.high for b in hrs), min(b.low for b in hrs), hrs[-1].close)
    assert days[0].start.weekday() == 0
    assert all(c.start.weekday() == 0 for c in build_candles(fake_hourly(24 * 30), "1w"))


def test_replay_matches_indicator_at_close():
    hourly = fake_hourly()
    for tf in TF_HOURS:
        candles = build_candles(hourly, tf)
        sigs = leledc(candles)
        recs = replay(candles, 10, 40)
        assert {r["i"]: r["final"] for r in recs if r["final"]} == {i: s for i, s in enumerate(sigs) if s}
    assert any(r["final"] for r in replay(build_candles(hourly, "4h"), 10, 40))


def test_run_length_and_report():
    assert run_length([0, 1, 1, 0, 1, 1, 1], 7, 1) == 3
    assert run_length([0, 1, 1, 0, 1, 1, 1], 3, 1) == 2
    hourly = fake_hourly()
    recs = {"X": {tf: replay(build_candles(hourly, tf), 10, 40) for tf in TF_HOURS}}
    text = report(recs, ["X"], 1.1)
    assert "## Daily" in text and "At the close" in text
