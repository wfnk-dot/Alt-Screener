"""TradingView watchlist file, Discord post and the full report."""
import json
from datetime import timedelta

import requests

from .tags import fmt_pct, rsi_text, trend_text

IMPORT_REMINDER = "Import: TradingView → Watchlist menu → **Import list** → pick `watchlist.txt`."


def week_of(day):
    """Monday the list is for: today if it's Monday, otherwise the coming Monday."""
    return day + timedelta(days=(7 - day.weekday()) % 7)


def week_label(monday):
    return f"{monday.day} {monday:%b}"


# ---------------------------------------------------------------- TradingView


def watchlist_text(top_coins, cfg):
    """TradingView import format: one line, comma-separated, '###Name' starts a section."""
    tv = cfg["tradingview"]
    parts = [f"###{tv['core_section']}", *tv["core"], f"###{tv['alts_section']}"]
    parts += [tv["alt_symbol_format"].format(symbol=c.symbol) for c in top_coins]
    return ",".join(parts) + "\n"


# ---------------------------------------------------------------- Discord


def diff_lists(current, previous):
    """(new this week, dropped off), keeping the order of each list."""
    if previous is None:
        return list(current), []
    prev, cur = set(previous), set(current)
    return [c for c in current if c not in prev], [c for c in previous if c not in cur]


def _table(coins):
    weeks = coins[0].tags.get("wrsi_weeks", 12) if coins else 12
    rows = [["#", "Coin", "Trend", "ATR%", "7d", "OI 7d", "wRSI", f"n/{weeks}"]]
    for i, c in enumerate(coins, 1):
        rsi, above = rsi_text(c.tags)
        rows.append(
            [
                str(i),
                c.base,
                trend_text(c.tags),
                fmt_pct(c.atr_pct, signed=False, digits=1),
                fmt_pct(c.return_7d_pct),
                fmt_pct(c.oi_change_pct),
                rsi,
                above,
            ]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    right = {0, 3, 4, 5, 6, 7}
    lines = []
    for r in rows:
        cells = [v.rjust(w) if i in right else v.ljust(w) for i, (v, w) in enumerate(zip(r, widths))]
        lines.append(" ".join(cells).rstrip())
    return "\n".join(lines)


def discord_message(top_coins, new, dropped, monday, notes=()):
    lines = [f"**Alt watchlist, week of {week_label(monday)}**", "```", _table(top_coins), "```"]
    lines.append("**New this week:** " + (", ".join(new) if new else "none"))
    lines.append("**Dropped off:** " + (", ".join(dropped) if dropped else "none"))
    extras = []
    for c in top_coins:
        bits = []
        if c.tags.get("new_listing"):
            bits.append("new listing")
        if c.tags.get("funding_extreme"):
            bits.append(f"extreme funding {c.funding_8h_pct:+.3f}%/8h")
        if bits:
            extras.append(f"{c.base}: {', '.join(bits)}")
    if extras:
        lines.append("**Flags:** " + "; ".join(extras))
    for n in notes:
        lines.append(f"_{n}_")
    lines.append(IMPORT_REMINDER)
    return "\n".join(lines)


def post_discord(webhook_url, content, attachment_name=None, attachment_text=None):
    payload = {"content": content[:2000], "allowed_mentions": {"parse": []}}
    files = None
    if attachment_text is not None:
        files = {"files[0]": (attachment_name, attachment_text.encode(), "text/plain")}
    r = requests.post(
        webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true",
        data={"payload_json": json.dumps(payload)},
        files=files,
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Discord webhook returned {r.status_code}: {r.text[:200]}")


# ---------------------------------------------------------------- report.md


def _money(v):
    if v is None:
        return "n/a"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:.1f}{unit}"
    return f"${v:.0f}"


def report_markdown(ranked, top_n, monday, run_info, rejected_counts, notes=()):
    weeks = ranked[0].tags.get("wrsi_weeks", 12) if ranked else 12
    out = [f"# Alt watchlist, week of {week_label(monday)}", ""]
    for k, v in run_info.items():
        out.append(f"- **{k}:** {v}")
    out.append("")
    out.append(f"## All {len(ranked)} coins that passed the filters (top {top_n} go on the watchlist)")
    out.append("")
    head = [
        "Rank", "Coin", "Score", "Trend", "ATR%", "Vol 7d/30d", "7d move", "OI 7d",
        "Binance OI", "Binance 7d avg vol", "HL 24h vol", "HL OI", "wRSI", f"n/{weeks}", "Funding /8h", "Tags", "Candles",
    ]
    out.append("| " + " | ".join(head) + " |")
    out.append("|" + "---|" * len(head))
    for i, c in enumerate(ranked, 1):
        rsi, above = rsi_text(c.tags)
        flags = []
        if c.tags.get("new_listing"):
            flags.append("New listing")
        if c.tags.get("funding_extreme"):
            flags.append("Extreme funding")
        funding = "n/a" if c.funding_8h_pct is None else f"{c.funding_8h_pct:+.4f}% ({c.funding_source})"
        row = [
            f"**{i}**" if i <= top_n else str(i),
            c.base,
            f"{c.score:.0f}",
            trend_text(c.tags),
            fmt_pct(c.atr_pct, signed=False, digits=1),
            "n/a" if c.vol_ratio is None else f"{c.vol_ratio:.2f}",
            fmt_pct(c.return_7d_pct, digits=1),
            fmt_pct(c.oi_change_pct, digits=1),
            _money(c.oi_usd),
            _money(c.avg_vol_7d),
            _money(c.hl_volume_24h),
            _money(c.hl_oi_usd),
            rsi,
            above,
            funding,
            ", ".join(flags),
            c.source,
        ]
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    out.append("## Coins knocked out, by filter")
    out.append("")
    out.append("A coin can fail more than one filter.")
    out.append("")
    for name, n in rejected_counts.items():
        out.append(f"- {name}: {n}")
    if notes:
        out.append("")
        out.append("## Notes")
        out.append("")
        out.extend(f"- {n}" for n in notes)
    out.append("")
    out.append("Tags are information only; they never filter. Not trade advice.")
    return "\n".join(out) + "\n"

