"""Info-only tags: trend (daily/weekly), weekly RSI, new listing, OI change, funding."""
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
    close = closes[-1]
    if (
        ef[-1] > es[-1]
        and _rising(ef, back)
        and _rising(es, back)
        and close > es[-1]
        and have_swings
        and ph[-1] > ph[-2]
        and pl[-1] > pl[-2]
    ):
        return "Up"
    if (
        ef[-1] < es[-1]
        and _falling(ef, back)
        and _falling(es, back)
        and close < es[-1]
        and have_swings
        and ph[-1] < ph[-2]
        and pl[-1] < pl[-2]
    ):
        return "Down"
    return "Range"


def weekly_rsi(weekly_bars, ind):
    """(latest weekly RSI, count of the last N weekly RSIs above the threshold, N). None when n/a."""
    rsis = rsi_series([b.close for b in weekly_bars], ind["rsi_period"])
    n = ind["rsi_lookback_weeks"]
    last = rsis[-1] if rsis else None
    window = rsis[-n:]
    count = None
    if len(window) == n and all(v is not None for v in window):
        count = sum(1 for v in window if v > ind["rsi_threshold"])
    return last, count, n


def tag_coin(coin, cfg):
    ind = cfg["indicators"]
    weekly = to_weekly(coin.bars)
    rsi, above, n = weekly_rsi(weekly, ind)
    t = {
        "trend_d": trend(coin.bars, ind["pivot_bars_daily"], ind, force_range_check=True),
        "trend_w": trend(weekly, ind["pivot_bars_weekly"], ind),
        "wrsi": rsi,
        "wrsi_above": above,
        "wrsi_weeks": n,
        "wrsi_threshold": ind["rsi_threshold"],
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
    return f"D: {t['trend_d']} · W: {t['trend_w']}"


def rsi_text(t):
    rsi = "n/a" if t["wrsi"] is None else f"{t['wrsi']:.0f}"
    above = "n/a" if t["wrsi_above"] is None else f"{t['wrsi_above']}/{t['wrsi_weeks']}"
    return rsi, above


def oi_text(coin):
    if coin.oi_change_pct is None:
        return "OI n/a"
    arrow = "↑" if coin.oi_change_pct >= 0 else "↓"
    return f"OI {arrow} {abs(coin.oi_change_pct):.0f}%"


def funding_text(coin):
    if coin.funding_8h_pct is None:
        return "Funding n/a"
    flag = " ⚠️ extreme" if coin.tags.get("funding_extreme") else ""
    return f"Funding {coin.funding_8h_pct:+.3f}%{flag}"


def tag_list(coin):
    t = coin.tags
    rsi, above = rsi_text(t)
    out = [trend_text(t), f"wRSI {rsi}", f"wRSI>{t['wrsi_threshold']:g} {above}", oi_text(coin), funding_text(coin)]
    if t["new_listing"]:
        out.append("New listing")
    return out
