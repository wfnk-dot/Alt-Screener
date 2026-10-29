"""Dorito alerts: Leledc exhaustion signals ('Exhaustion levels (InSilico)') on closed candles,
posted to Discord. Runs every hour; each timeframe is only checked once a new candle has closed.

Candles come from Binance's live futures data via www.binance.com (the same prices as the
TradingView chart), with Hyperliquid as a backup. Coins are the top N by Hyperliquid volume that also
trade as Binance USDT perps.
"""
import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import ROOT, load_config
from .data import (
    Bar,
    BinanceArchive,
    Http,
    Throttle,
    binance_web_klines,
    hyperliquid_candles,
    hyperliquid_coins,
    parallel_map,
)
from .image import GREEN, MUTED, RED, TEXT, TREND_COLOURS, render_card
from .indicators import to_weekly
from .output import post_discord
from .tags import colour_states
from .universe import base_from_symbol, is_excluded, match_hyperliquid

log = logging.getLogger("alerts")
STATE = ROOT / "alerts_state.json"
TF_LABEL = {"4h": "4h", "1d": "Daily", "3d": "3-day", "1w": "Weekly"}
TF_HOURS = {"4h": 4, "1d": 24, "3d": 72, "1w": 168}


def leledc(bars, count=10, length=40):
    """Exhaustion signal on each bar, exactly as the Pine script: -1 = red Dorito above the bar
    (top), 1 = green Dorito below (bottom), 0 = none.

    The script keeps two running counts that only reset when they fire: candles closing above
    the close 4 bars back, and candles closing below it.
    """
    up = down = 0
    out = []
    for i, b in enumerate(bars):
        sig = 0
        if i >= 4:
            if b.close > bars[i - 4].close:
                up += 1
            if b.close < bars[i - 4].close:
                down += 1
        if i >= length - 1:
            window = bars[i - length + 1 : i + 1]
            if up > count and b.close < b.open and b.high >= max(x.high for x in window):
                up = 0
                sig = -1
            elif down > count and b.close > b.open and b.low <= min(x.low for x in window):
                down = 0
                sig = 1
        out.append(sig)
    return out


def to_3day(daily):
    """3-day candles from daily ones, grouped like TradingView's 3D chart: blocks start on days where
    days-since-1-Jan-1970 divides by 3 (e.g. 29 Mar and 23 Aug 2026). Checked against Wyn's chart;
    Binance's own 3d files start a day later. Only complete blocks are kept."""
    groups = {}
    for b in daily:
        days = b.day.toordinal() - date(1970, 1, 1).toordinal()
        groups.setdefault(days // 3, []).append(b)
    out = []
    for k in sorted(groups):
        g = groups[k]
        if len(g) == 3:
            out.append(Bar(g[0].day, g[0].open, max(x.high for x in g), min(x.low for x in g), g[-1].close,
                           sum(x.quote_volume for x in g)))
    return out


def candle_start(b):
    """Start time of a candle as a UTC datetime (daily-type bars carry a date)."""
    if isinstance(b.day, datetime):
        return b.day
    return datetime(b.day.year, b.day.month, b.day.day, tzinfo=timezone.utc)


def closed_series(daily, h4, now):
    """{timeframe: closed candles} built from Hyperliquid daily and 4h candles."""
    days = [b for b in daily if candle_start(b) + timedelta(days=1) <= now]
    weeks = [w for w in to_weekly(days) if candle_start(w) + timedelta(days=7) <= now]
    return {"4h": h4, "1d": days, "3d": to_3day(days), "1w": weeks}


def new_signals(series, last_seen, cfg, now):
    """Signals on candles that closed after `last_seen` (ISO start time per timeframe).
    Returns (signals, updated last_seen). For a coin seen for the first time, only a candle that
    closed at the latest close time counts (no alerts on old candles)."""
    a = cfg["alerts"]
    found = []
    seen = dict(last_seen)
    for tf, bars in series.items():
        if tf not in a["timeframes"] or not bars:
            continue
        sigs = leledc(bars, a["leledc_bar_count"], a["leledc_swing_length"])
        cutoff = last_seen.get(tf)
        latest_close = _last_close(tf, now)
        for i in range(len(bars)):
            started = candle_start(bars[i]).isoformat()
            if cutoff is not None and started <= cutoff:
                continue
            if cutoff is None and candle_start(bars[i]) + timedelta(hours=TF_HOURS[tf]) < latest_close:
                continue
            if sigs[i]:
                found.append((tf, bars[i], sigs[i]))
        seen[tf] = candle_start(bars[-1]).isoformat()
    return found, seen


TF_ORDER = {"1w": 0, "3d": 1, "1d": 2, "4h": 3}


def alert_records(coin, found, series, price_now, ind):
    """One dict per new Dorito, with the context shown on the card."""
    out = []
    for tf, bar, sig in found:
        bars = series[tf]
        states, _, _ = colour_states([b.close for b in bars], ind)
        idx = next(i for i, b in enumerate(bars) if b is bar)
        level = bar.high if sig == -1 else bar.low
        out.append({
            "coin": coin, "tf": tf, "sig": sig, "level": level, "price": price_now,
            "bands": states[idx],
            "closed": candle_start(bar) + timedelta(hours=TF_HOURS[tf]),
        })
    return out


def _fmt_price(v):
    if v is None:
        return "n/a"
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:.4g}" if v < 100 else f"{v:.2f}"
    return f"{v:.4g}"


def alert_card(records, tz, now, source="Binance futures"):
    """The alert picture, in the same style as the watchlist."""
    records = sorted(records, key=lambda r: (TF_ORDER[r["tf"]], r["sig"], r["coin"]))
    columns = [("Coin", "", "left", True), ("Time", "frame", "left", False), ("Dorito", "", "center", False),
               ("Level", "high / low", "right", False), ("Price", "now", "right", False),
               ("From", "level", "right", False), ("Bands", "XO 12/25", "center", False),
               ("Candle", "closed (UK)", "right", False)]
    rows = []
    for r in records:
        top = r["sig"] == -1
        dist = None if not r["price"] or not r["level"] else (r["price"] / r["level"] - 1) * 100
        closed = r["closed"].astimezone(tz)
        rows.append([
            (r["coin"], TEXT),
            (TF_LABEL[r["tf"]], TEXT),
            ("pill", "Top" if top else "Bottom", RED if top else GREEN),
            (_fmt_price(r["level"]), TEXT),
            (_fmt_price(r["price"]), TEXT),
            ("n/a", MUTED) if dist is None else (f"{dist:+.1f}%", GREEN if dist > 0 else RED if dist < 0 else TEXT),
            ("pill", {1: "Green", -1: "Red"}.get(r["bands"], "n/a"),
             {1: GREEN, -1: RED}.get(r["bands"], TREND_COLOURS["Range"])),
            (closed.strftime("%a %d %b %H:%M") if r["tf"] == "4h" else closed.strftime("%a %d %b"), MUTED),
        ])
    n = len(records)
    footer = [
        "Confirmed on candle close. Top = Leledc exhaustion: a red candle making a 40-candle high after a run up. "
        "Bottom = the mirror.",
        f"Bands = XO EMA 12/25 colour on that timeframe at the Dorito.  Prices: {source}.",
    ]
    return render_card(f"Dorito alerts · {n} new", f"Checked {now.astimezone(tz):%a %d %b %H:%M} UK", columns, rows, footer)


def alert_text(records, ping):
    """Short text above the picture, so the phone notification says what fired."""
    parts = []
    for tf in sorted({r["tf"] for r in records}, key=TF_ORDER.get):
        names = [f"{r['coin']} {'▼' if r['sig'] == -1 else '▲'}" for r in records if r["tf"] == tf]
        parts.append(f"{TF_LABEL[tf]}: " + ", ".join(names))
    text = "**Dorito alerts** · " + " · ".join(parts)
    return (ping + "\n" + text) if ping else text


def pick_coins(cfg, http, hl):
    """Top N Hyperliquid coins by 24h volume that also trade as Binance USDT perps."""
    a = cfg["alerts"]
    archive = BinanceArchive(http, cfg["data"]["binance_archive_url"])
    groups = cfg["universe"]["exclude"]
    excluded = {str(n).upper() for g in a["exclude_groups"] for n in (groups.get(g) or [])}
    bases = [b for b in (base_from_symbol(s, cfg["universe"]["quote_asset"]) for s in archive.list_perp_symbols()) if b]
    live = {n: c for n, c in hl.items() if not c.delisted}
    matched = match_hyperliquid(bases, list(live), cfg["universe"].get("hyperliquid_aliases"))
    # One Binance symbol per Hyperliquid coin (prefer the plain name)
    by_hl = {}
    for base, name in sorted(matched.items(), key=lambda kv: len(kv[0])):
        if not is_excluded(base, excluded):
            by_hl.setdefault(name, base)
    ranked = sorted(by_hl, key=lambda n: -live[n].volume_24h)
    quote = cfg["universe"]["quote_asset"]
    return [(by_hl[n], n, by_hl[n] + quote) for n in ranked[: a["top_n"]]]


def run(cfg, dry_run=False, now=None):
    a, d = cfg["alerts"], cfg["data"]
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    state = json.loads(STATE.read_text()) if STATE.exists() else {}

    # Which timeframes have a newly closed candle since the last check?
    due = []
    for tf in a["timeframes"]:
        last = state.get("_checked", {}).get(tf)
        boundary = _last_close(tf, now)
        if last is None or last < boundary.isoformat():
            due.append(tf)
    if not due:
        log.info("No new candle closes since the last check")
        return []

    http = Http(timeout=d["timeout_seconds"], pool=d["parallel_downloads"])
    hl = hyperliquid_coins(http, d["hyperliquid_url"])
    coins = pick_coins(cfg, http, hl)
    log.info("Checking %s on %d coins", ", ".join(due), len(coins))
    hl_throttle = Throttle(d["hyperliquid_seconds_between_calls"])
    bn_throttle = Throttle(a["binance_seconds_between_calls"])
    now_ms = int(now.timestamp() * 1000)
    sources = {}

    bn_failures = []  # after 3 failures in a row, skip Binance for the rest of the run

    def fetch(coin, interval, days):
        base, name, symbol = coin
        try:
            if len(bn_failures) < 3:
                bars = binance_web_klines(http, a["binance_web_url"], symbol, interval, pages=2, throttle=bn_throttle)
                if bars:
                    bn_failures.clear()
                    sources[base] = "Binance"
                    return bars
        except Exception as e:  # noqa: BLE001
            bn_failures.append(symbol)
            log.warning("Binance %s candles failed for %s, using Hyperliquid: %s", interval, symbol, e)
        try:
            sources[base] = "Hyperliquid"
            return hyperliquid_candles(http, d["hyperliquid_url"], name, now_ms - days * 86_400_000, now_ms, interval, hl_throttle)
        except Exception as e:  # noqa: BLE001
            log.warning("Hyperliquid %s candles failed for %s: %s", interval, name, e)
            return None

    need_daily = any(tf in due for tf in ("1d", "3d", "1w"))
    daily = parallel_map(lambda c: fetch(c, "1d", 4900), coins, 4) if need_daily else {}
    h4 = parallel_map(lambda c: fetch(c, "4h", 800), coins, 4) if "4h" in due else {}

    records, failed = [], []
    for coin in coins:
        base, name, _ = coin
        if (need_daily and daily.get(coin) is None) or ("4h" in due and h4.get(coin) is None):
            failed.append(base)
            continue
        series = closed_series(daily.get(coin) or [], h4.get(coin) or [], now)
        series = {tf: bars for tf, bars in series.items() if tf in due}
        found, seen = new_signals(series, state.get(name, {}), cfg, now)
        state[name] = {**state.get(name, {}), **seen}
        raw = daily.get(coin) or h4.get(coin) or []
        records += alert_records(base, found, series, raw[-1].close if raw else None, cfg["indicators"])
    for tf in due:
        state.setdefault("_checked", {})[tf] = _last_close(tf, now).isoformat()

    if records:
        text = alert_text(records, a.get("ping", ""))
        hl_for = sorted(b for b, src in sources.items() if src == "Hyperliquid")
        source = "Binance futures" + (f" (Hyperliquid for {', '.join(hl_for)})" if hl_for else "")
        png = alert_card(records, tz, now, source)
        (ROOT / "latest").mkdir(exist_ok=True)
        (ROOT / "latest" / "doritos.png").write_bytes(png)
        if dry_run:
            print(text)
        else:
            url = os.environ.get("DISCORD_ALERTS_WEBHOOK_URL")
            if not url:
                raise RuntimeError("DISCORD_ALERTS_WEBHOOK_URL secret is not set (see SETUP.md)")
            post_discord(url, text[:1900], [("doritos.png", png, "image/png")], ping=a.get("ping", ""))
    else:
        log.info("No new Doritos")
    hl_used = sorted(b for b, src in sources.items() if src == "Hyperliquid")
    if hl_used:
        log.warning("Used Hyperliquid candles (Binance unavailable) for: %s", ", ".join(hl_used))
    if failed:
        log.warning("Couldn't load candles for: %s", ", ".join(failed))
    if not dry_run:
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    return records


def _last_close(tf, now):
    """Close time of the most recent candle of this timeframe that has closed by `now` (UTC)."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if tf == "4h":
        return now.replace(hour=now.hour - now.hour % 4, minute=0, second=0, microsecond=0)
    if tf == "1d":
        return midnight
    if tf == "3d":
        days = (midnight.date() - date(1970, 1, 1)).days
        return midnight - timedelta(days=days % 3)
    return midnight - timedelta(days=midnight.weekday())  # weekly: Monday 00:00 UTC


def main(argv=None):
    p = argparse.ArgumentParser(description="Dorito (Leledc exhaustion) alerts")
    p.add_argument("--dry-run", action="store_true", help="print alerts instead of posting, don't save state")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(load_config(), dry_run=args.dry_run)
    except Exception as e:
        log.exception("Alerts failed")
        (ROOT / "run_error.txt").write_text(f"Dorito alerts: {type(e).__name__}: {e}"[:300], encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
