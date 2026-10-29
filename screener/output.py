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


def week_title(monday):
    """'Alt watchlist: Mon 2 Nov – Sun 8 Nov'."""
    sunday = monday + timedelta(days=6)
    return f"Alt watchlist: Mon {week_label(monday)} – Sun {week_label(sunday)}"


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


def btc_summary(btc):
    """'BTC: daily trend Down, weekly trend Range, -3.1% this week'."""
    t = btc.tags
    move = "" if btc.return_7d_pct is None else f"  ·  {btc.return_7d_pct:+.1f}% in 7 days"
    return f"BTC trend: 1h {t['trend_1h']}, 4h {t['trend_4h']}, daily {t['trend_d']}, weekly {t['trend_w']}{move}"


def discord_message(top_coins, new, dropped, monday, cfg, btc_line="", notes=()):
    """Short text that goes above the table image."""
    lines = []
    ping = (cfg.get("discord") or {}).get("ping") or ""
    if ping:
        lines.append(ping)
    lines.append(f"**{week_title(monday)}**")
    if btc_line:
        lines.append(btc_line)
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


def post_discord(webhook_url, content, attachments=(), ping=""):
    """attachments: list of (filename, bytes, content type)."""
    # Discord only pings @here/@everyone if the post explicitly allows it
    parse = ["everyone"] if ping in ("@here", "@everyone") else []
    payload = {"content": content[:2000], "allowed_mentions": {"parse": parse}}
    files = {f"files[{i}]": a for i, a in enumerate(attachments)}
    r = requests.post(
        webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true",
        data={"payload_json": json.dumps(payload)},
        files=files or None,
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
    # 4h/1h trends are only fetched for the coins on the list
    out = [f"# {week_title(monday)}", ""]
    for k, v in run_info.items():
        out.append(f"- **{k}:** {v}")
    out.append("")
    out.append(f"## All {len(ranked)} coins that passed the filters (top {top_n} go on the watchlist)")
    out.append("")
    head = [
        "Rank", "Coin", "Score", "Trend", "ATR%", "Vol 7d/30d", "7d move", "Weekend move", "OI 3d", "OI 7d",
        "Corr BTC", "Beta BTC", "Binance OI", "Binance 7d avg vol", "HL 24h vol", "HL OI", "wRSI", f"wRSI {weeks}w low–high", "Funding /8h", "Tags", "Candles",
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
            fmt_pct(c.return_weekend_pct, digits=1),
            fmt_pct(c.oi_change_3d_pct, digits=1),
            fmt_pct(c.oi_change_pct, digits=1),
            "n/a" if c.corr_btc is None else f"{c.corr_btc:.2f}",
            "n/a" if c.beta_btc is None else f"{c.beta_btc:.2f}",
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

