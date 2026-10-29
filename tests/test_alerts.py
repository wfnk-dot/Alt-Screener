from datetime import date, datetime, timedelta, timezone

from screener.alerts import _last_close, closed_series, leledc, new_signals, to_3day
from screener.data import Bar


def bar(i, o, c, h=None, l=None, start=date(2026, 1, 1)):
    return Bar(start + timedelta(days=i), o, h if h is not None else max(o, c), l if l is not None else min(o, c), c, 1.0)


def rally_then_red_top(n_up=50):
    """Steady rally (every close above the close 4 back), then a red candle that makes a new high."""
    bars = [bar(i, 100 + i, 101 + i) for i in range(n_up)]
    top = 101 + n_up + 5
    bars.append(bar(n_up, 100 + n_up + 2, 100 + n_up - 1, h=top))  # red candle, highest high
    return bars


def test_top_dorito_on_exhaustion():
    sigs = leledc(rally_then_red_top())
    assert sigs[-1] == -1
    assert sigs[:-1].count(-1) == 0


def test_no_dorito_on_green_candle_or_lower_high():
    bars = rally_then_red_top()
    green = bars[:-1] + [bar(50, 150, 160, h=200)]
    assert leledc(green)[-1] == 0
    lower = bars[:-1] + [bar(50, 152, 149, h=120)]  # red but not the 40-bar high
    assert leledc(lower)[-1] == 0


def test_count_must_exceed_threshold_and_resets():
    # Only 9 rising closes before the red top: count not above 10 -> nothing
    flat = [bar(i, 100, 100) for i in range(40)]
    up = [bar(40 + i, 100 + i, 101 + i) for i in range(9)]
    bars = flat + up + [bar(49, 112, 108, h=130)]
    assert leledc(bars)[-1] == 0
    # A second red top right after the first: count was reset to 0, so no repeat
    bars = rally_then_red_top()
    again = bars + [bar(51, 160, 155, h=170)]
    assert leledc(again)[-1] == 0


def test_bottom_dorito_mirror():
    bars = [bar(i, 200 - i, 199 - i) for i in range(50)]
    bars.append(bar(50, 148, 152, l=140))  # green candle, lowest low
    assert leledc(bars)[-1] == 1


def test_3day_blocks_match_binance():
    # TradingView 3D candles on Wyn's ENA chart opened 2026-03-29 and 2026-08-23
    daily = [bar(i, 1, 1, start=date(2026, 3, 28)) for i in range(9)]
    blocks = to_3day(daily)
    assert [b.day for b in blocks] == [date(2026, 3, 29), date(2026, 4, 1)]  # incomplete edges dropped
    assert to_3day([bar(i, 1, 1, start=date(2026, 8, 23)) for i in range(3)])[0].day == date(2026, 8, 23)


def test_last_close_times():
    now = datetime(2026, 10, 29, 13, 3, tzinfo=timezone.utc)  # Thursday
    assert _last_close("4h", now) == datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    assert _last_close("1d", now) == datetime(2026, 10, 29, tzinfo=timezone.utc)
    assert _last_close("1w", now) == datetime(2026, 10, 26, tzinfo=timezone.utc)
    assert _last_close("3d", now) == datetime(2026, 10, 28, tzinfo=timezone.utc)


def test_only_closed_candles_and_no_repeats(cfg):
    bars = rally_then_red_top()  # last (red top) candle is 2026-02-20
    forming_day = bars[-1].day
    # Mid-candle: the top candle hasn't closed yet, so no alert
    now = datetime(forming_day.year, forming_day.month, forming_day.day, 15, tzinfo=timezone.utc)
    series = closed_series(bars, [], now)
    found, seen = new_signals({"1d": series["1d"]}, {"1d": (bars[-3].day.isoformat() + "T00:00:00+00:00")}, cfg, now)
    assert found == []
    # Once it has closed: one alert
    now += timedelta(hours=10)
    series = closed_series(bars, [], now)
    found, seen = new_signals({"1d": series["1d"]}, seen, cfg, now)
    assert [(tf, s) for tf, _, s in found] == [("1d", -1)]
    # Next hour: already seen, no repeat
    found, _ = new_signals({"1d": series["1d"]}, seen, cfg, now + timedelta(hours=1))
    assert found == []


def test_new_coin_does_not_alert_old_candles(cfg):
    bars = rally_then_red_top()
    now = datetime.combine(bars[-1].day + timedelta(days=5), datetime.min.time(), timezone.utc)
    series = closed_series(bars, [], now)
    found, seen = new_signals({"1d": series["1d"]}, {}, cfg, now)
    assert found == [] and "1d" in seen
