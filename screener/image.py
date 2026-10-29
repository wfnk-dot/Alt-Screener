"""Draws the weekly table as a PNG for Discord. Which columns show is set in config.yml (table: columns)."""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).parent / "fonts"
S = 2  # draw at 2x so it stays sharp on phones

BG = (24, 25, 29)
ROW_ALT = (31, 33, 38)
LINE = (48, 51, 58)
TEXT = (232, 233, 236)
MUTED = (140, 146, 158)
GREEN = (74, 222, 128)
RED = (248, 113, 113)
AMBER = (251, 191, 36)
TREND_COLOURS = {"Up": GREEN, "Down": RED, "Range": (160, 166, 178), "n/a": (110, 115, 125)}


def _font(weight, size):
    return ImageFont.truetype(str(FONTS / f"Inter-{weight}.otf"), size * S)


def _pct(v, digits=1, signed=True):
    if v is None:
        return "n/a", MUTED
    text = f"{v:+.{digits}f}%" if signed else f"{v:.{digits}f}%"
    if not signed:
        return text, TEXT
    return text, GREEN if v > 0 else RED if v < 0 else TEXT


def _money(v):
    if v is None:
        return "n/a"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:.1f}{unit}"
    return f"${v:.0f}"


def _funding(c):
    if c.funding_8h_pct is None:
        return "n/a", MUTED
    return f"{c.funding_8h_pct:+.3f}%", AMBER if c.tags.get("funding_extreme") else TEXT


def _rsi(c):
    v = c.tags.get("wrsi")
    return ("n/a", MUTED) if v is None else (f"{v:.0f}", TEXT)


def _rsi_range(c):
    low = c.tags.get("wrsi_low")
    return ("n/a", MUTED) if low is None else (f"{low:.0f}–{c.tags['wrsi_high']:.0f}", TEXT)


# key: (header line 1, header line 2, alignment, value function -> (text, colour) or ("pill", trend))
COLUMNS = {
    "rank": ("#", "", "right", lambda i, c: (str(i), MUTED)),
    "coin": ("Coin", "", "left", lambda i, c: (c.base, TEXT)),
    "trend_weekly": ("Trend", "weekly", "center", lambda i, c: ("pill", c.tags.get("trend_w", "n/a"))),
    "trend_daily": ("Trend", "daily", "center", lambda i, c: ("pill", c.tags.get("trend_d", "n/a"))),
    "trend_4h": ("Trend", "4h", "center", lambda i, c: ("pill", c.tags.get("trend_4h", "n/a"))),
    "trend_1h": ("Trend", "1h", "center", lambda i, c: ("pill", c.tags.get("trend_1h", "n/a"))),
    "atr": ("ATR", "daily %", "right", lambda i, c: _pct(c.atr_pct, signed=False)),
    "price_7d": ("Price", "7 days", "right", lambda i, c: _pct(c.return_7d_pct)),
    "price_weekend": ("Price", "weekend", "right", lambda i, c: _pct(c.return_weekend_pct)),
    "oi_3d": ("OI", "3 days", "right", lambda i, c: _pct(c.oi_change_3d_pct)),
    "oi_7d": ("OI", "7 days", "right", lambda i, c: _pct(c.oi_change_pct)),
    "rsi_weekly": ("RSI", "weekly", "right", lambda i, c: _rsi(c)),
    "rsi_range": ("RSI 12w", "low–high", "right", lambda i, c: _rsi_range(c)),
    "funding": ("Funding", "per 8h", "right", lambda i, c: _funding(c)),
    "oi_size": ("OI", "size", "right", lambda i, c: (_money(c.oi_usd), TEXT)),
    "volume_ratio": ("Volume", "7d vs 30d", "right",
                     lambda i, c: ("n/a", MUTED) if c.vol_ratio is None else (f"{c.vol_ratio:.2f}x", TEXT)),
    "corr_btc": ("Corr", "vs BTC", "right",
                 lambda i, c: ("n/a", MUTED) if c.corr_btc is None else (f"{c.corr_btc:.2f}", TEXT)),
    "beta_btc": ("Beta", "vs BTC", "right",
                 lambda i, c: ("n/a", MUTED) if c.beta_btc is None else (f"{c.beta_btc:.1f}x", TEXT)),
    "score": ("Score", "", "right", lambda i, c: ("n/a", MUTED) if c.score is None else (f"{c.score:.0f}", TEXT)),
}

LEGEND = ("OI = open interest (money in open positions).  ATR = average daily move.  "
          "RSI low–high = weekly RSI range over the last 12 weeks.  Corr = how closely it follows BTC (1 = exactly).  "
          "Beta 2x = moves about 2% when BTC moves 1%.")


def render_table(coins, cfg, title, subtitle="", btc_line=""):
    keys = [k for k in cfg["table"]["columns"] if k in COLUMNS]
    body, bold = _font("Regular", 15), _font("SemiBold", 15)
    head = _font("SemiBold", 12)
    title_font, small = _font("SemiBold", 20), _font("Regular", 11)
    pad, gap, row_h, pill_pad = 24 * S, 22 * S, 36 * S, 9 * S

    # Header text uses the configured RSI lookback
    weeks = cfg["indicators"]["rsi_lookback_weeks"]
    headers = {k: COLUMNS[k][:2] for k in keys}
    if "rsi_range" in headers:
        headers["rsi_range"] = (f"RSI {weeks}w", "low–high")
    legend = LEGEND.replace("12 weeks", f"{weeks} weeks")

    cells = []
    for i, c in enumerate(coins, 1):
        cells.append([COLUMNS[k][3](i, c) for k in keys])

    def width(cell, key):
        text, extra = cell
        if text == "pill":
            return bold.getlength(extra) + 2 * pill_pad
        return (bold if key == "coin" else body).getlength(text)

    col_w = []
    for j, k in enumerate(keys):
        w = max(head.getlength(headers[k][0]), head.getlength(headers[k][1]))
        for row in cells:
            w = max(w, width(row[j], k))
        col_w.append(int(w))

    table_w = sum(col_w) + gap * (len(keys) - 1)
    parts = [p.strip() for p in legend.split("  ") if p.strip()]
    legend_lines = [" ".join(parts[:3]), " ".join(parts[3:])]
    lines_below = [x for x in (subtitle, *legend_lines) if x]
    total_w = max(table_w, int(max([title_font.getlength(title)] + [small.getlength(x) for x in lines_below]))) + 2 * pad
    title_h = (64 if btc_line else 40) * S
    head_h = 40 * S
    foot_h = (18 * S) * len(lines_below) + 12 * S
    total_h = pad + title_h + head_h + row_h * len(cells) + foot_h + pad

    img = Image.new("RGB", (total_w, total_h), BG)
    d = ImageDraw.Draw(img)
    d.text((pad, pad), title, font=title_font, fill=TEXT)
    if btc_line:
        d.text((pad, pad + 30 * S), btc_line, font=_font("Regular", 14), fill=MUTED)

    y = pad + title_h
    x = pad
    for j, k in enumerate(keys):
        align = COLUMNS[k][2]
        for line_no, text in enumerate(headers[k]):
            tw = head.getlength(text)
            tx = x + (col_w[j] - tw if align == "right" else (col_w[j] - tw) / 2 if align == "center" else 0)
            d.text((tx, y + line_no * 16 * S), text, font=head, fill=MUTED)
        x += col_w[j] + gap
    y += head_h
    d.line([(pad, y - 6 * S), (pad + table_w, y - 6 * S)], fill=LINE, width=S)

    for r, row in enumerate(cells):
        if r % 2 == 1:
            d.rectangle([(pad - 10 * S, y), (pad + table_w + 10 * S, y + row_h)], fill=ROW_ALT)
        mid = y + row_h / 2
        x = pad
        for j, k in enumerate(keys):
            align = COLUMNS[k][2]
            text, extra = row[j]
            if text == "pill":
                colour = TREND_COLOURS.get(extra, MUTED)
                pw = bold.getlength(extra) + 2 * pill_pad
                px = x + (col_w[j] - pw) / 2
                fill = tuple(int(BG[n] * 0.8 + colour[n] * 0.2) for n in range(3))
                d.rounded_rectangle([(px, mid - 12 * S), (px + pw, mid + 12 * S)], radius=12 * S, fill=fill)
                d.text((px + pw / 2, mid), extra, font=bold, fill=colour, anchor="mm")
            else:
                font = bold if k == "coin" else body
                tw = font.getlength(text)
                tx = x + (col_w[j] - tw if align == "right" else (col_w[j] - tw) / 2 if align == "center" else 0)
                d.text((tx, mid), text, font=font, fill=extra, anchor="lm")
            x += col_w[j] + gap
        y += row_h

    y += 12 * S
    for line in lines_below:
        d.text((pad, y), line, font=small, fill=MUTED)
        y += 18 * S

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
