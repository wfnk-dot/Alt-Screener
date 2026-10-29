import random

from screener.indicators import pivots, to_weekly
from screener.tags import in_range_band, tag_coin, trend
from screener.metrics import Coin
from tests.conftest import make_bars, zigzag


def test_uptrend(cfg):
    bars = make_bars(zigzag(120, 0.01))
    assert trend(bars, 3, cfg["indicators"], force_range_check=True) == "Up"


def test_downtrend(cfg):
    bars = make_bars(zigzag(120, -0.01))
    assert trend(bars, 3, cfg["indicators"], force_range_check=True) == "Down"


def test_flat_range(cfg):
    bars = make_bars(zigzag(120, 0.0))
    assert trend(bars, 3, cfg["indicators"], force_range_check=True) == "Range"


def test_choppy_is_range(cfg):
    rnd = random.Random(7)
    closes = [100.0]
    for _ in range(150):
        closes.append(closes[-1] * (1 + rnd.uniform(-0.03, 0.03)))
    closes = [100 + (c - 100) * 0.1 for c in closes]  # tight chop
    assert trend(make_bars(closes), 3, cfg["indicators"], force_range_check=True) == "Range"


def test_range_band_forces_range(cfg):
    ind = cfg["indicators"]
    # Gentle grind up with big wicks: EMAs and swings say Up, but the closes
    # sit inside 2.5 x ATR, so the daily tag is forced to Range
    closes = [100 * 1.0005 ** i for i in range(120)]
    bars = make_bars(closes, wick=0.05)
    assert in_range_band(bars, ind)
    assert trend(bars, 3, ind, force_range_check=True) == "Range"


def test_not_enough_history(cfg):
    assert trend(make_bars(zigzag(20, 0.01)), 3, cfg["indicators"]) == "n/a"


def test_weekly_trend_and_tags(cfg):
    bars = make_bars(zigzag(500, 0.004, amp=0.15, period=60))
    coin = Coin("XUSDT", "X", "X", bars)
    t = tag_coin(coin, cfg)
    assert t["trend_w"] == "Up"
    assert t["new_listing"] is False


def test_new_listing(cfg):
    coin = Coin("XUSDT", "X", "X", make_bars(zigzag(40, 0.01)))
    t = tag_coin(coin, cfg)
    assert t["new_listing"] is True
    assert t["trend_w"] == "n/a" and t["wrsi"] is None


def test_pivots():
    v = [1, 2, 3, 2, 1, 2, 5, 2, 1, 0, 1]
    assert pivots(v, 2, "high") == [2, 6]
    assert pivots(v, 2, "low") == [4]
    assert pivots([1, 3, 3, 1, 0], 1, "high") == [1]  # flat top counts once


def test_weekly_resample_mondays():
    bars = make_bars([float(i) for i in range(1, 15)])  # starts Monday 6 Jan 2025
    w = to_weekly(bars)
    assert [x.day.weekday() for x in w] == [0, 0]
    assert w[0].close == 7.0 and w[1].open == 7.0 and w[1].close == 14.0
    assert w[0].quote_volume == 7e6
