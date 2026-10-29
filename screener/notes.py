"""Short written notes per coin (plain rules, no AI): when the XO bands turned, the breakout that
started it and its volume, and whether price is still holding off the fast EMA."""
from .indicators import to_weekly
from .tags import colour_states


def _date(bar, weekly):
    return f"w/c {bar.day.day} {bar.day:%b}" if weekly else f"{bar.day.day} {bar.day:%b}"


def timeframe_note(bars, ind, weekly):
    """One sentence about the current band colour on this timeframe, or None if too little history."""
    unit = "week" if weekly else "day"
    closes = [b.close for b in bars]
    states, fast, slow = colour_states(closes, ind)
    if not states or states[-1] not in (1, -1):
        return None
    up = states[-1] == 1
    start = len(states) - 1
    while start > 0 and states[start - 1] == states[-1]:
        start -= 1
    run = len(states) - start
    parts = [f"bands {'green' if up else 'red'} since {_date(bars[start], weekly)} ({run} {unit}{'s' if run != 1 else ''})"]

    # The breakout: the first close beyond both EMAs that led into this run (often before the colour flip)
    def beyond(k):
        if fast[k] is None or slow[k] is None:
            return False
        return closes[k] > max(fast[k], slow[k]) if up else closes[k] < min(fast[k], slow[k])

    b = start
    while b - 1 >= 0 and beyond(b - 1):
        b -= 1
    if beyond(b):
        prev = [x.quote_volume for x in bars[max(0, b - 20):b]]
        mult = bars[b].quote_volume / (sum(prev) / len(prev)) if prev and sum(prev) else None
        vol = f" on {mult:.1f}x normal volume" if mult else ""
        parts.append(f"broke {'above' if up else 'below'} the bands {_date(bars[b], weekly)}{vol}")

    # Holding: candles since price last touched the fast EMA
    touched = None
    for k in range(len(bars) - 1, start - 1, -1):
        if fast[k] is not None and (bars[k].low <= fast[k] if up else bars[k].high >= fast[k]):
            touched = len(bars) - 1 - k
            break
    if touched is None:
        parts.append("hasn't touched the fast EMA since the flip")
    elif touched == 0:
        parts.append("testing the fast EMA now")
    elif touched >= 2:
        parts.append(f"hasn't touched the fast EMA in {touched} {unit}s")
    else:
        parts.append(f"tapped the fast EMA last {unit}")
    return "; ".join(parts)


def coin_note(coin, cfg):
    ind = cfg["indicators"]
    t = coin.tags
    out = []
    for label, bars, weekly, trend in (("Weekly", to_weekly(coin.bars), True, t.get("trend_w")),
                                       ("Daily", coin.bars, False, t.get("trend_d"))):
        note = timeframe_note(bars, ind, weekly)
        if note:
            out.append(f"{label} ({trend}): {note}.")
    return " ".join(out)


def notes_messages(top, cfg, limit=1900):
    """Discord messages with one tap-to-reveal (spoiler) note per coin."""
    lines = ["**Notes** · tap a grey bar to reveal"]
    for i, c in enumerate(top, 1):
        note = coin_note(c, cfg)
        if note:
            lines.append(f"**{i}. {c.base}** ||{note}||")
    msgs, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > limit:
            msgs.append(cur.rstrip())
            cur = ""
        cur += line + "\n"
    return msgs + ([cur.rstrip()] if cur.strip() else [])
