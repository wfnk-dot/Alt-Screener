"""Info-only tags: trend (weekly/daily/4h/1h), weekly RSI, new listing, funding."""
from .indicators import atr, ema, pivots, rsi_series, to_weekly


def _rising(series, bars_back):
    return series[-1] is not None and series[-1 - bars_back] is not None and series[-1] > series[-1 - bars_back]


def _falling(series, bars_back):
    return series[-1] is not None and series[-1 - bars_back] is not None and series[-1] < series[-1 - bars_back]


def in_range_band(bars, ind):
    """True when the last N closes all sit inside a band `multiple` x ATR wide around their mean."""
    n = ind["range_lookback_days"]
    a = atr(bars, ind["atr_period"])
    if len(bars) < n or a is None:
        return False
    closes = [b.close for b in bars[-n:]]
    mean = sum(closes) / n
    half_band = ind["range_band_atr_multiple"] * a / 2
    return max(abs(c - mean) for c in closes) < half_band


def trend(bars, pivot_n, ind, force_range_check=False):
    """'Up', 'Down', 'Range', or 'n/a' (not enough history)."""
    fast, slow, back = ind["ema_fast"], ind["ema_slow"], ind["ema_rising_bars"]
    if len(bars) < slow + back:
        return "n/a"
    if force_range_check and in_range_band(bars, ind):
        return "Range"
    closes = [b.close for b in bars]
    ef, es = ema(closes, fast), ema(closes, slow)
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    ph = [highs[i] for i in pivots(highs, pivot_n, "high")]
    pl = [lows[i] for i in pivots(lows, pivot_n, "low")]
    have_swings = len(ph) >= 2 and len(pl) >= 2
    # Up = EMA12 above EMA25, both rising, and higher swing highs + higher swing lows. Down = mirror.
    if (
        ef[-1] > es[-1]
        and _rising(ef, back)
        and _rising(es, back)
        and have_swings
        and ph[-1] > ph[-2]
        and pl[-1] > pl[-2]
    ):
        return "Up"
    if (
        ef[-1] < es[-1]
        and _falling(ef, back)
        and _falling(es, back)
        and have_swings
        and ph[-1] < ph[-2]
        and pl[-1] < pl[-2]
    ):
        return "Down"
    return "Range"


def weekly_rsi(weekly_bars, ind):
    """(latest weekly RSI, lowest and highest weekly RSI over the last N weeks). None when n/a.
    The low shows how deep the resets went; the high shows how strong it got."""
    rsis = rsi_series([b.close for b in weekly_bars], ind["rsi_period"])
    n = ind["rsi_lookback_weeks"]
    last = rsis[-1] if rsis else None
    window = rsis[-n:]
    if len(window) == n and all(v is not None for v in window):
        return last, min(window), max(window)
    return last, None, None


def tag_coin(coin, cfg, intraday=None):
    """intraday: {"4h": bars, "1h": bars} of closed candles, or None if not fetched."""
    ind = cfg["indicators"]
    weekly = to_weekly(coin.bars)
    rsi, low, high = weekly_rsi(weekly, ind)
    intraday = intraday or {}
    t = {
        "trend_w": trend(weekly, ind["pivot_bars_weekly"], ind),
        "trend_d": trend(coin.bars, ind["pivot_bars_daily"], ind, force_range_check=True),
        "trend_4h": trend(intraday["4h"], ind["pivot_bars_4h"], ind) if intraday.get("4h") else "n/a",
        "trend_1h": trend(intraday["1h"], ind["pivot_bars_1h"], ind) if intraday.get("1h") else "n/a",
        "wrsi": rsi,
        "wrsi_low": low,
        "wrsi_high": high,
        "wrsi_weeks": ind["rsi_lookback_weeks"],
        "new_listing": len(weekly) < ind["new_listing_min_weeks"],
        "funding_extreme": coin.funding_8h_pct is not None
        and abs(coin.funding_8h_pct) >= ind["funding_extreme_pct"],
    }
    coin.tags = t
    return t


# ---- text helpers shared by the Discord post and the report


def fmt_pct(v, signed=True, digits=0):
    if v is None:
        return "n/a"
    return f"{v:+.{digits}f}%" if signed else f"{v:.{digits}f}%"


def trend_text(t):
    return f"1h: {t['trend_1h']} · 4h: {t['trend_4h']} · D: {t['trend_d']} · W: {t['trend_w']}"


def rsi_text(t):
    """('58', '47-71')"""
    rsi = "n/a" if t["wrsi"] is None else f"{t['wrsi']:.0f}"
    rng = "n/a" if t["wrsi_low"] is None else f"{t['wrsi_low']:.0f}–{t['wrsi_high']:.0f}"
    return rsi, rng
