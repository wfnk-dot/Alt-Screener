"""Plain-Python indicators: EMA, Wilder ATR and RSI, swing pivots, weekly candles."""
from datetime import timedelta

from .data import Bar


def ema(values, period):
    """Exponential moving average, seeded with the simple average of the first `period` values.
    Returns a list the same length as `values` with None until the seed."""
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def atr(bars, period=14):
    """Wilder's Average True Range for the latest bar, or None if there aren't enough bars."""
    if len(bars) < period + 1:
        return None
    trs = []
    for prev, b in zip(bars, bars[1:]):
        trs.append(max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close)))
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def atr_pct(bars, period=14):
    a = atr(bars, period)
    return None if a is None or not bars[-1].close else a / bars[-1].close * 100


def rsi_series(closes, period=14):
    """Wilder's RSI. Returns a list the same length as `closes`, None until enough data."""
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains, losses = [], []
    for a, b in zip(closes, closes[1:]):
        ch = b - a
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period

    def value(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g / l)

    out[period] = value(avg_g, avg_l)
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i + 1] = value(avg_g, avg_l)
    return out


def pivots(values, n, kind):
    """Indexes of confirmed swing highs (kind='high') or lows (kind='low').

    A swing is the highest (lowest) point within `n` bars on each side. On a flat
    top, only the first bar counts. The last `n` bars can't be confirmed yet.
    """
    better = (lambda a, b: a > b) if kind == "high" else (lambda a, b: a < b)
    out = []
    for i in range(n, len(values) - n):
        v = values[i]
        left = values[i - n : i]
        right = values[i + 1 : i + n + 1]
        if all(better(v, x) for x in left) and all(not better(x, v) for x in right):
            out.append(i)
    return out


def to_weekly(bars):
    """Group daily candles into Monday-starting weeks (same as Binance/TradingView).
    The current, unfinished week is included as the last candle."""
    weeks = []
    for b in bars:
        start = b.day - timedelta(days=b.day.weekday())
        if weeks and weeks[-1].day == start:
            w = weeks[-1]
            w.high = max(w.high, b.high)
            w.low = min(w.low, b.low)
            w.close = b.close
            w.quote_volume += b.quote_volume
        else:
            weeks.append(Bar(start, b.open, b.high, b.low, b.close, b.quote_volume))
    return weeks
