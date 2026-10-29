"""Runs the weekly scan end to end."""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT, load_config
from .data import (
    BinanceArchive,
    BinanceLive,
    Http,
    OIPoint,
    binance_oi_history,
    binance_web_klines,
    bybit_klines,
    Throttle,
    hyperliquid_candles,
    hyperliquid_coins,
    parallel_map,
)
from .image import render_table
from .filters import apply_filters
from .metrics import Coin, beta_corr, compute_metrics, live_price_changes
from .output import diff_lists, discord_message, post_discord, report_markdown, btc_summary, watchlist_text, week_of, week_title
from .ranking import score_and_rank
from .state import current_list, load_state, previous_list, record_week, save_state
from .tags import tag_coin
from .universe import base_from_symbol, excluded_names, is_excluded, match_hyperliquid

log = logging.getLogger("screener")

# Binance gives perpetuals a delivery date of 2100 unless they're being delisted
_PERPETUAL_DELIVERY_FLOOR_MS = 4_000_000_000_000


def allowed_from_exchange_info(info, quote):
    """Symbols that are live, perpetual, crypto (not index/tradfi) and not scheduled for delisting."""
    out = set()
    for s in info.get("symbols", []):
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
            and s.get("quoteAsset") == quote
            and s.get("underlyingType", "COIN") == "COIN"
            and int(s.get("deliveryDate") or 0) >= _PERPETUAL_DELIVERY_FLOOR_MS
        ):
            out.add(s["symbol"])
    return out


def has_gaps(bars, recent_days=60):
    recent = bars[-recent_days:]
    return any((b.day - a.day).days != 1 for a, b in zip(recent, recent[1:]))


def build_universe(cfg, archive, live, hl, notes):
    quote = cfg["universe"]["quote_asset"]
    info = live.exchange_info()
    if info:
        # Live list: only trading perps, not index/tradfi, not scheduled for delisting
        symbols = sorted(allowed_from_exchange_info(info, quote))
    else:
        symbols = archive.list_perp_symbols()
        notes.append("Binance live API not reachable: coin list from the archive, "
                     "delisted contracts detected by missing recent candles instead.")
    excluded = excluded_names(cfg)
    bases = {}
    for sym in symbols:
        base = base_from_symbol(sym, quote)
        if base and not is_excluded(base, excluded):
            bases[base] = sym
    hl_live = [name for name, c in hl.items() if not c.delisted]
    matched = match_hyperliquid(list(bases), hl_live, cfg["universe"].get("hyperliquid_aliases"))
    return [(bases[b], b, hl_name) for b, hl_name in sorted(matched.items())]


def run(cfg, today, dry_run=False, scheduled=False, root=ROOT, now_uk=None, midweek=None):
    """midweek: None for the weekly post; otherwise the mid-week slot being run ("" for a manual run)."""
    d = cfg["data"]
    http = Http(timeout=d["timeout_seconds"], pool=d["parallel_downloads"])
    archive = BinanceArchive(http, d["binance_archive_url"])
    live = BinanceLive(http, d["binance_api_url"])
    notes = []

    log.info("Hyperliquid: fetching coins")
    hl = hyperliquid_coins(http, d["hyperliquid_url"])
    universe = build_universe(cfg, archive, live, hl, notes)
    log.info("Universe: %d Binance perps also on Hyperliquid", len(universe))

    coins = []
    for sym, base, hl_name in universe:
        h = hl[hl_name]
        coins.append(Coin(symbol=sym, base=base, hl_name=hl_name, bars=[], hl_volume_24h=h.volume_24h, hl_oi_usd=h.oi_usd))

    # Hyperliquid checks need no downloads, so do them first to save time.
    # Coins failing them would fail the full filter anyway.
    f = cfg["filters"]
    rejected = {"Hyperliquid volume": 0, "Hyperliquid OI": 0}
    candidates = []
    for c in coins:
        ok = True
        if c.hl_volume_24h < f["hyperliquid_min_volume_24h"]:
            rejected["Hyperliquid volume"] += 1
            ok = False
        if c.hl_oi_usd < f["hyperliquid_min_open_interest"]:
            rejected["Hyperliquid OI"] += 1
            ok = False
        if ok:
            candidates.append(c)
    log.info("Downloading candles for %d coins", len(candidates))

    btc_symbol = cfg["btc"]["binance_symbol"]
    bn = d["binance_api_url"]
    bn_throttle = Throttle(d["binance_seconds_between_calls"])
    now_utc = datetime.now(timezone.utc)

    # After a few live Binance failures in a row, stop trying it for the rest of the run
    bn_failures = []

    def binance_ok():
        return len(bn_failures) < 3

    def binance_failed(what, e):
        bn_failures.append(what)
        log.warning("Binance live %s failed, using the backup: %s", what, e)

    def daily_candles(sym):
        """Live Binance candles (the last one still forming); the archive if Binance doesn't answer."""
        if binance_ok():
            try:
                bars = binance_web_klines(http, bn, sym, "1d", pages=1, throttle=bn_throttle)
                if bars:
                    bn_failures.clear()
                    return bars, "Binance live"
            except Exception as e:  # noqa: BLE001
                binance_failed(f"candles for {sym}", e)
        return archive.klines(sym, today, d["history_months"]), "Binance archive"

    klines = parallel_map(daily_candles, [c.symbol for c in candidates] + [btc_symbol], 4)
    live_daily = {sym: bars for sym, (bars, src) in klines.items() if src == "Binance live"}
    btc_bars = [b for b in klines[btc_symbol][0] if b.day < today]
    for c in candidates:
        bars, c.source = klines[c.symbol]
        c.bars = [b for b in bars if b.day < today]
    newest = max((c.bars[-1].day for c in candidates if c.bars), default=today - timedelta(days=1))

    # Bybit fallback when the archive is behind or has holes for a coin
    bybit_used = []
    for c in candidates:
        if c.source == "Binance live" or (c.bars and c.bars[-1].day >= newest and not has_gaps(c.bars)):
            continue
        try:
            bars = [b for b in bybit_klines(http, d["bybit_url"], c.symbol) if b.day < today]
        except Exception as e:  # noqa: BLE001
            log.warning("Bybit fallback failed for %s: %s", c.symbol, e)
            continue
        if bars and (not c.bars or bars[-1].day >= c.bars[-1].day):
            c.bars, c.source = bars, "Bybit"
            bybit_used.append(c.base)
    if bybit_used:
        notes.append("Candles from Bybit (Binance archive incomplete) for: " + ", ".join(bybit_used))

    stale = cfg["universe"]["max_days_since_last_candle"]
    live_coins = [c for c in candidates if c.bars and (newest - c.bars[-1].day).days <= stale]
    rejected["No recent candles (delisted/settling)"] = len(candidates) - len(live_coins)

    log.info("Downloading open interest for %d coins", len(live_coins))
    ch = cfg["indicators"]["change_days"]
    short = cfg["indicators"]["oi_short_change_days"]

    def open_interest(sym):
        """(now, {days: earlier}): live Binance hourly snapshots, else the archive's daily ones."""
        try:
            hist = binance_oi_history(http, bn, sym, "1h", 200, bn_throttle) if binance_ok() else None
            if hist:
                t_now, c_now, v_now = hist[-1]
                before = {}
                for days in (short, ch):
                    target = t_now - timedelta(days=days)
                    t, c_, v = min(hist, key=lambda h: abs(h[0] - target))
                    before[days] = OIPoint(t, c_, v) if abs(t - target) <= timedelta(hours=3) else None
                return OIPoint(t_now, c_now, v_now), before
        except Exception as e:  # noqa: BLE001
            binance_failed(f"OI for {sym}", e)
        return archive.open_interest(sym, today - timedelta(days=1), [short, ch])

    oi = parallel_map(open_interest, [c.symbol for c in live_coins], 4)
    oi_when = None  # newest reading: a datetime (live) or a date (archive)
    for c in live_coins:
        now, before = oi[c.symbol]
        if now:
            c.oi_usd = now.value_usd
            oi_when = now.day if oi_when is None else oi_when
            if before.get(ch) and before[ch].contracts:
                c.oi_change_pct = (now.contracts / before[ch].contracts - 1) * 100
            if before.get(short) and before[short].contracts:
                c.oi_change_3d_pct = (now.contracts / before[short].contracts - 1) * 100

    funding = live.funding_8h()
    for c in live_coins:
        if funding and c.symbol in funding:
            c.funding_8h_pct, c.funding_source = funding[c.symbol], "Binance"
        else:
            c.funding_8h_pct, c.funding_source = hl[c.hl_name].funding_8h_pct, "Hyperliquid"
    if not funding:
        notes.append("Funding from Hyperliquid (Binance live API not reachable).")

    passed = []
    for name in ("Binance 7d volume", "Binance OI", "ATR%"):
        rejected[name] = 0
    for c in live_coins:
        compute_metrics(c, cfg)
        c.beta_btc, c.corr_btc = beta_corr(c.bars, btc_bars, cfg["indicators"]["beta_corr_days"])
        if apply_filters(c, cfg):
            passed.append(c)
        for reason in c.failed:
            rejected[reason] = rejected.get(reason, 0) + 1
    log.info("%d coins passed the filters", len(passed))
    if not passed:
        raise RuntimeError("no coins passed the filters (data problem?)")

    # Live prices: Binance's forming daily candle (so the weekend is included); Hyperliquid if missing
    hl_throttle = Throttle(d["hyperliquid_seconds_between_calls"])
    now_ms = int(now_utc.timestamp() * 1000)

    def hl_bars(name, interval, days):
        start = now_ms - days * 86_400_000
        try:
            return hyperliquid_candles(http, d["hyperliquid_url"], name, start, now_ms, interval, hl_throttle)
        except Exception as e:  # noqa: BLE001
            log.warning("Hyperliquid %s candles failed for %s: %s", interval, name, e)
            return []

    btc_hl = cfg["btc"]["hyperliquid_coin"]
    for c in passed:
        if c.symbol in live_daily:
            live_price_changes(c, live_daily[c.symbol], today, "Binance (live)")
        else:
            live_price_changes(c, hl_bars(c.hl_name, "1d", 10), today)

    ranked = score_and_rank(passed, cfg["ranking_weights"])
    top = ranked[: cfg["top_n"]]

    # 4h and 1h trends: only for the coins on the list (and BTC), to keep requests down
    ind = cfg["indicators"]
    btc = Coin(btc_symbol, "BTC", btc_hl, btc_bars)
    intraday_source = set()

    def intraday(pair, interval):
        """Closed 4h/1h candles: live Binance, else Hyperliquid."""
        symbol, hl_name = pair
        step = timedelta(hours=4 if interval == "4h" else 1)
        try:
            bars = binance_web_klines(http, bn, symbol, interval, pages=1, throttle=bn_throttle) if binance_ok() else []
            bars = [b for b in bars if b.day + step <= now_utc][-ind["intraday_candles"]:]
            if bars:
                intraday_source.add("Binance")
                return bars
        except Exception as e:  # noqa: BLE001
            binance_failed(f"{interval} candles for {symbol}", e)
        intraday_source.add("Hyperliquid")
        return hl_bars(hl_name, interval, ind["intraday_candles"] * (4 if interval == "4h" else 1) // 24 + 1)

    pairs = [(c.symbol, c.hl_name) for c in top] + [(btc_symbol, btc_hl)]
    h4 = parallel_map(lambda p: intraday(p, "4h"), pairs, 4)
    h1 = parallel_map(lambda p: intraday(p, "1h"), pairs, 4)
    for c in ranked:
        key = (c.symbol, c.hl_name)
        tag_coin(c, cfg, {"4h": h4.get(key), "1h": h1.get(key)})

    # BTC's own trend, shown at the top of the post
    compute_metrics(btc, cfg)
    if btc_symbol in live_daily:
        live_price_changes(btc, live_daily[btc_symbol], today, "Binance (live)")
    else:
        live_price_changes(btc, hl_bars(btc_hl, "1d", 10), today)
    tag_coin(btc, cfg, {"4h": h4[(btc_symbol, btc_hl)], "1h": h1[(btc_symbol, btc_hl)]})
    btc_line = btc_summary(btc)
    if midweek is not None:
        return run_midweek(cfg, top, btc_line, today, dry_run, scheduled, root, now_uk, midweek)

    monday = week_of(today if now_uk is None else now_uk.date())
    week_key = monday.isoformat()
    state_path = Path(root) / "state.json"
    state = load_state(state_path)
    new, dropped = diff_lists([c.base for c in top], previous_list(state, week_key))

    now_uk = now_uk or datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))
    wl = watchlist_text(top, cfg)
    msg = discord_message(top, new, dropped, monday, cfg, btc_line=btc_line, notes=[n for n in notes if n.startswith("Candles from Bybit")])
    price_src = sorted({c.price_source for c in top})
    if any("live" in p for p in price_src):
        prices = f"Prices: {' / '.join(price_src)} at {now_uk:%a %d %b %H:%M} UK."
    else:
        prices = f"Prices: Binance daily candles to {newest:%a %d %b}."
    if isinstance(oi_when, datetime):
        oi_text = f"OI: Binance, {oi_when.astimezone(now_uk.tzinfo):%a %d %b %H:%M} UK."
    elif oi_when:
        oi_text = f"OI: Binance archive, to end of {oi_when:%a %d %b}."
    else:
        oi_text = ""
    candles = "live Binance" if live_daily else f"Binance archive to {newest:%a %d %b}"
    footer = (f"{prices}  {oi_text}  ATR, weekly/daily trend: {candles} (closed candles).  "
              f"4h/1h trend: {' / '.join(sorted(intraday_source)) or 'n/a'}, live.")
    png = render_table(top, cfg, title=week_title(monday), subtitle=footer, btc_line=btc_line)
    run_info = {
        "Generated (UK time)": now_uk.strftime("%a %d %b %Y %H:%M"),
        "Daily candles up to (UTC)": newest.isoformat(),
        "Binance perps also on Hyperliquid (after exclusions)": len(universe),
        "Passed all filters": len(passed),
    }
    report = report_markdown(ranked, cfg["top_n"], monday, run_info, rejected, notes)

    latest = Path(root) / "latest"
    history = Path(root) / "history"
    latest.mkdir(exist_ok=True)
    history.mkdir(exist_ok=True)
    (latest / "watchlist.txt").write_text(wl, encoding="utf-8")
    (latest / "report.md").write_text(report, encoding="utf-8")
    (latest / "table.png").write_bytes(png)
    (history / f"{week_key}-watchlist.txt").write_text(wl, encoding="utf-8")

    if dry_run:
        print("---- Discord post (dry run, not sent) ----")
        print(msg)
        print("---- watchlist.txt ----")
        print(wl)
    else:
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        if not url:
            raise RuntimeError("DISCORD_WEBHOOK_URL secret is not set (see SETUP.md)")
        post_discord(url, msg, [("table.png", png, "image/png"), ("watchlist.txt", wl.encode(), "text/plain")],
                     ping=cfg["discord"]["ping"])
        record_week(state, week_key, [c.base for c in top], cfg["output"]["keep_weeks"], scheduled)
        save_state(state_path, state)
    return top, ranked


def run_midweek(cfg, top, btc_line, today, dry_run, scheduled, root, now_uk, slot):
    """Ping the watchlist channel about coins that have entered the top N since the current list."""
    state_path = Path(root) / "state.json"
    state = load_state(state_path)
    week_key, current = current_list(state, today)
    pinged = state["midweek_pinged"].get(week_key, [])
    movers = [c for c in top if c.base not in current and c.base not in pinged]
    if scheduled:
        state["last_midweek_slot"] = slot
    if not movers:
        log.info("Mid-week check: no new coins in the top %d", cfg["top_n"])
        if not dry_run:
            save_state(state_path, state)
        return top, movers
    now_uk = now_uk or datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))
    names = ", ".join(c.base for c in movers)
    ping = cfg["discord"].get("ping") or ""
    text = (f"{ping}\n" if ping else "") + (
        f"**Mid-week movers** · {names} {'has' if len(movers) == 1 else 'have'} joined the top {cfg['top_n']} since Sunday"
    )
    png = render_table(movers, cfg, title=f"Mid-week movers · {now_uk:%a %d %b}", btc_line=btc_line,
                       subtitle=f"Joined the top {cfg['top_n']} since Sunday's list. Prices live at {now_uk:%H:%M} UK.")
    # Updated TradingView file: this week's list plus the new coins in their own section
    tv = cfg["tradingview"]
    current_syms = [tv["alt_symbol_format"].format(symbol=c) for c in [_symbol_for(b, top) for b in current] if c]
    parts = [f"###{tv['core_section']}", *tv["core"], f"###{tv['alts_section']}", *current_syms, "###NEW",
             *[tv["alt_symbol_format"].format(symbol=c.symbol) for c in movers]]
    wl = ",".join(parts) + "\n"
    latest = Path(root) / "latest"
    latest.mkdir(exist_ok=True)
    (latest / "midweek.png").write_bytes(png)
    if dry_run:
        print("---- Mid-week post (dry run, not sent) ----")
        print(text)
        print(wl)
        return top, movers
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        raise RuntimeError("DISCORD_WEBHOOK_URL secret is not set (see SETUP.md)")
    post_discord(url, text, [("movers.png", png, "image/png"), ("watchlist.txt", wl.encode(), "text/plain")], ping=ping)
    state["midweek_pinged"][week_key] = pinged + [c.base for c in movers]
    for k in sorted(state["midweek_pinged"])[:-cfg["output"]["keep_weeks"]]:
        del state["midweek_pinged"][k]
    save_state(state_path, state)
    return top, movers


def _symbol_for(base, coins):
    """Binance symbol for a saved coin name (USDT perps: base + USDT)."""
    for c in coins:
        if c.base == base:
            return c.symbol
    return f"{base}USDT"


def main(argv=None):
    p = argparse.ArgumentParser(description="Weekly alt perp watchlist")
    p.add_argument("--dry-run", action="store_true", help="print the Discord post instead of sending it")
    p.add_argument("--scheduled", action="store_true", help="mark this as the weekly scheduled run")
    p.add_argument("--midweek", nargs="?", const="", default=None,
                   help="mid-week movers check instead of the weekly post (optionally the schedule slot)")
    p.add_argument("--config", default=str(ROOT / "config.yml"))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    now_uk = datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))
    today = datetime.now(timezone.utc).date()
    try:
        run(cfg, today, dry_run=args.dry_run, scheduled=args.scheduled, now_uk=now_uk, midweek=args.midweek)
    except Exception as e:
        log.exception("Screener failed")
        (ROOT / "run_error.txt").write_text(f"{type(e).__name__}: {e}"[:300], encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

