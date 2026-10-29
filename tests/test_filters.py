from screener.filters import apply_filters
from screener.metrics import Coin, compute_metrics
from tests.conftest import make_bars, zigzag


def good_coin(**kw):
    c = Coin("ABCUSDT", "ABC", "ABC", make_bars(zigzag(60, 0.0, amp=0.1, period=6)))
    for b in c.bars:
        b.quote_volume = 50e6
    c.oi_usd, c.hl_volume_24h, c.hl_oi_usd = 20e6, 5e6, 5e6
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_passes(cfg):
    c = compute_metrics(good_coin(), cfg)
    assert c.atr_pct >= 4
    assert apply_filters(c, cfg), c.failed


def test_each_threshold(cfg):
    cases = {
        "Binance OI": dict(oi_usd=14e6),
        "Hyperliquid volume": dict(hl_volume_24h=2.9e6),
        "Hyperliquid OI": dict(hl_oi_usd=2.9e6),
    }
    for reason, kw in cases.items():
        c = compute_metrics(good_coin(**kw), cfg)
        assert not apply_filters(c, cfg)
        assert c.failed == [reason]


def test_volume_uses_last_7_days(cfg):
    c = good_coin()
    for b in c.bars[-7:]:
        b.quote_volume = 29e6
    compute_metrics(c, cfg)
    assert not apply_filters(c, cfg) and c.failed == ["Binance 7d volume"]


def test_low_atr_fails(cfg):
    c = good_coin(bars=make_bars([100 + 0.1 * i for i in range(60)], wick=0.001))
    for b in c.bars:
        b.quote_volume = 50e6
    compute_metrics(c, cfg)
    assert not apply_filters(c, cfg) and c.failed == ["ATR%"]


def test_missing_oi_fails(cfg):
    c = compute_metrics(good_coin(oi_usd=None), cfg)
    assert not apply_filters(c, cfg)


def test_brand_new_coin_skips_atr(cfg):
    c = good_coin(bars=make_bars([100.0, 100.1, 100.2, 100.1, 100.0], wick=0.001))
    for b in c.bars:
        b.quote_volume = 50e6
    compute_metrics(c, cfg)
    assert c.atr_pct is None and c.vol_ratio is None and c.return_7d_pct is None
    assert apply_filters(c, cfg)


def test_metrics(cfg):
    c = good_coin()
    for i, b in enumerate(c.bars):
        b.quote_volume = 20e6 if i >= len(c.bars) - 7 else 10e6
    compute_metrics(c, cfg)
    assert c.avg_vol_7d == 20e6
    assert abs(c.vol_ratio - 20 / ((7 * 20 + 23 * 10) / 30)) < 1e-9
    assert abs(c.return_7d_pct - (c.bars[-1].close / c.bars[-8].close - 1) * 100) < 1e-9


def test_live_price_changes():
    from datetime import date

    from screener.data import Bar
    from screener.metrics import live_price_changes

    today = date(2026, 11, 1)  # Sunday
    closes = {date(2026, 10, 25): 100.0, date(2026, 10, 30): 110.0, date(2026, 10, 31): 120.0, today: 99.0}
    bars = [Bar(d, 0, 0, 0, c, 0) for d, c in sorted(closes.items())]
    bars[2].open = 110.0  # Saturday opens at Friday's close
    c = live_price_changes(Coin("X", "X", "X", []), bars, today)
    assert round(c.return_7d_pct, 6) == -1.0          # 100 -> 99
    assert round(c.return_weekend_pct, 6) == -10.0    # Sat open 110 -> Sun now 99
    assert c.price_source == "Hyperliquid (live)"
    # Midweek: weekend = last Saturday + Sunday, complete
    stale = live_price_changes(Coin("X", "X", "X", []), bars[:-1], today)
    assert stale.return_7d_pct is None  # no candle for today: leave the archive numbers alone
