"""Info-only tags: trend (weekly/daily/4h/1h), weekly RSI, new listing, funding."""
from .indicators import atr, ema, rsi_series, to_weekly


def colour_states(closes, ind):
    """+1 where EMA fast is above EMA slow (green bands), -1 below (red), None before both exist.
    Same colouring as the XO Bands indicator."""
    f, s = ema(closes, ind["ema_fast"]), ema(closes, ind["ema_slow"])
    out = []
    for a, b in zip(f, s):
        out.append(None if a is None or b is None else (1 if a > b else -1 if a < b else 0))
    return out, f, s


def trend(bars, ind):
    """'Up', 'Down', 'Range', or 'n/a' (not enough history).

    Up: bands green, at most `trend_max_flips` colour flips in the last `trend_flip_lookback` candles,
    and the bands open (EMA gap at least `trend_min_gap_atr` x ATR). Down: the same, red.
    Anything else (flip-flopping, flat or squeezed bands, a fresh flip) is Range.
    """
    if len(bars) < ind["ema_slow"] + 2:
        return "n/a"
    states, f, s = colour_states([b.close for b in bars], ind)
    window = [x for x in states[-ind["trend_flip_lookback"]:] if x is not None]
    flips = sum(1 for a, b in zip(window, window[1:]) if a != b)
    a = atr(bars, ind["atr_period"]) or atr(bars, max(2, len(bars) - 2))
    if not a or flips > ind["trend_max_flips"]:
        return "Range"
    gap = (f[-1] - s[-1]) / a
    if states[-1] == 1 and gap >= ind["trend_min_gap_atr"]:
        return "Up"
    if states[-1] == -1 and -gap >= ind["trend_min_gap_atr"]:
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
        "trend_w": trend(weekly, ind),
        "trend_d": trend(coin.bars, ind),
        "trend_4h": trend(intraday["4h"], ind) if intraday.get("4h") else "n/a",
        "trend_1h": trend(intraday["1h"], ind) if intraday.get("1h") else "n/a",
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
