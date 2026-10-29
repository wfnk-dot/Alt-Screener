import pytest

from screener.indicators import rsi_series
from screener.tags import weekly_rsi
from tests.conftest import make_bars

# Wilder's RSI(14) worked example as published by StockCharts ("ChartSchool: RSI").
# Their table uses unrounded closes, so values agree to within ~0.1.
CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
          46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18,
          44.22, 44.57, 43.42, 42.66, 43.13]
REFERENCE = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99,
             41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def test_rsi_matches_reference():
    got = rsi_series(CLOSES, 14)
    assert got[:14] == [None] * 14
    assert [round(x, 2) for x in got[14:]] == pytest.approx(REFERENCE, abs=0.1)


def test_rsi_first_value_by_hand():
    # first 14 changes: average gain / average loss, no smoothing yet
    ch = [b - a for a, b in zip(CLOSES, CLOSES[1:15])]
    g = sum(x for x in ch if x > 0) / 14
    l = -sum(x for x in ch if x < 0) / 14
    assert rsi_series(CLOSES, 14)[14] == pytest.approx(100 - 100 / (1 + g / l))


def test_rsi_extremes():
    assert rsi_series(list(range(1, 20)), 14)[-1] == 100.0
    assert rsi_series(list(range(20, 1, -1)), 14)[-1] == 0.0
    assert rsi_series([5.0] * 20, 14)[-1] == 50.0


def test_weekly_rsi_range(cfg):
    ind = cfg["indicators"]
    rsi, low, high = weekly_rsi(make_bars([100 + i for i in range(40)]), ind)  # steadily rising
    assert rsi == low == high == 100.0
    # Uptrend with one sharp pullback: the dip shows up as the low
    closes = [100 * 1.03 ** i for i in range(30)] + [c * 0.97 ** 3 for c in [100 * 1.03 ** 29]] + [100 * 1.03 ** 26 * 1.03 ** i for i in range(1, 9)]
    rsi, low, high = weekly_rsi(make_bars(closes), ind)
    assert low < rsi <= high and low < 80
    rsi, low, high = weekly_rsi(make_bars([100 + i for i in range(20)]), ind)
    assert rsi == 100.0 and low is None and high is None  # only 6 RSI values: range is n/a
