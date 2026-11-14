"""Dorito study: are Doritos good invalidation levels, and does a lower-timeframe band flip with the stop
behind the Dorito make a decent trade?

Built from ~3 years of Binance 15-minute candles. For every confirmed Dorito (4h / 12h / daily / 3-day / weekly):

1. Level test: how often price breaks back through the Dorito level, and how far price rotates away from
   the level before it does.
2. Trade test: after the Dorito closes, enter on the first lower-timeframe candle that closes with the XO bands
   the Dorito's way (red for a top, green for a bottom). Stop just past the Dorito level. Results in R.
   Compared against the same entry with a normal recent swing high/low as the stop and no Dorito (the
   "no Dorito" baseline), so we can see whether the Dorito adds anything.

Everything is split by BTC / big alts / other alts, the bands on the Dorito's timeframe and the one above,
and whether the Dorito candle was a quick wick that had already given most of the move back.
Run it on GitHub (Actions -> Dorito Study -> Run workflow); the report lands in latest/dorito_study.md.
"""
import argparse
import bisect
import logging
import math
import random
import statistics
import sys
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone

from .alerts import leledc_states, partial_signal, pick_coins
from .config import ROOT, load_config
from .data import BinanceArchive, Http, Throttle, binance_web_klines, hyperliquid_coins, parallel_map
from .indicators import ema

log = logging.getLogger("dorito_study")

M15 = 15 * 60 * 1000
MIN = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "12h": 720, "1d": 1440, "3d": 4320, "1w": 10080}
LABEL = {"15m": "15m", "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h", "12h": "12h", "1d": "Daily",
         "3d": "3-day", "1w": "Weekly"}
# Dorito timeframe -> the two lower timeframes whose band flip is used to get in
ENTRY_TFS = {"4h": ("15m", "1h"), "12h": ("30m", "2h"), "1d": ("1h", "4h"), "3d": ("4h", "12h"), "1w": ("12h", "1d")}
HIGHER = {"4h": "1d", "12h": "1d", "1d": "1w", "3d": "1w", "1w": None}  # "the timeframe above" for the trend split
BUFFER = 0.002          # stop 0.2% past the level
FEES_PCT = 0.10         # round-trip taker fees + a little slippage, % of price
MIN_RISK_PCT = 0.2      # skip entries whose stop is closer than this (fees would eat them)
LEVEL_CANDLES = 10      # level test: watch this many Dorito-timeframe candles after the close
ENTRY_WAIT = 3          # trade test: the band flip must come within this many Dorito-timeframe candles
TRADE_CANDLES = 20      # a trade still open after this many Dorito-timeframe candles is closed at market
TARGETS = (2, 3, 5)
CONTROL_PER_COIN = 120  # "no Dorito" baseline trades sampled per coin per setup
MIN_N = 100             # fewer trades than this = "too few to trust"
OOS_DAYS = 365          # the last year is kept back to check results on data the patterns weren't found on

C = namedtuple("C", "start open high low close last")  # candle; `last` = index of its final 15m bar


# ---------------------------------------------------------------- candles
def group_id(ts_ms, tf):
    m = MIN[tf]
    if tf == "1w":  # weeks start Monday 00:00 UTC (1 Jan 1970 was a Thursday)
        return (ts_ms // 86_400_000 - 4) // 7
    return ts_ms // (m * 60_000)  # also gives TradingView's 3D blocks (days since 1970 divisible by 3)


def aggregate(bars, tf):
    """Candles of `tf` from 15m bars (ts, o, h, l, c). Only complete candles are kept."""
    need = MIN[tf] // 15
    out, cur, gid = [], [], None
    for i, b in enumerate(bars):
        g = group_id(b[0], tf)
        if g != gid:
            if len(cur) == need:
                out.append(_candle(bars, cur))
            cur, gid = [], g
        cur.append(i)
    if len(cur) == need:
        out.append(_candle(bars, cur))
    return out


def _candle(bars, idx):
    first, last = bars[idx[0]], bars[idx[-1]]
    return C(first[0], first[1], max(bars[i][2] for i in idx), min(bars[i][3] for i in idx), last[4], idx[-1])


def band_states(candles):
    """XO bands colour per candle: +1 green (EMA 12 above 25), -1 red, None before both exist."""
    closes = [c.close for c in candles]
    f, s = ema(closes, 12), ema(closes, 25)
    return [None if a is None or b is None else (1 if a > b else -1 if a < b else 0) for a, b in zip(f, s)]


# ---------------------------------------------------------------- trades
def simulate(bars, start, entry, stop, d, max_bars):
    """Walk 15m bars from `start` (the bar after entry). d = +1 long, -1 short.
    Returns (risk %, best R reached before the stop, stopped?, R at the end)."""
    risk = abs(entry - stop)
    best, end = 0.0, len(bars) - 1
    last = min(end, start + max_bars - 1)
    for i in range(start, last + 1):
        _, _, h, l, c = bars[i]
        if (d == 1 and l <= stop) or (d == -1 and h >= stop):  # stop first if both in one bar (worst case)
            return risk / entry * 100, best, True, -1.0
        best = max(best, ((h - entry) if d == 1 else (entry - l)) / risk)
    final = ((bars[last][4] - entry) * d) / risk if last >= start else 0.0
    return risk / entry * 100, best, False, final


def outcome(t, target):
    """R for a trade with a fixed target (fees included)."""
    risk, best, stopped, final = t["risk"], t["best"], t["stopped"], t["final"]
    r = target if best >= target else (-1.0 if stopped else min(final, target))
    return r - FEES_PCT / risk


# ---------------------------------------------------------------- one coin
def stop_at(level, d):
    return level * (1 - BUFFER) if d == 1 else level * (1 + BUFFER)


def crossed_stop(bar, stop_px, d):
    return bar[3] <= stop_px if d == 1 else bar[2] >= stop_px


def risk_ok(entry, stop_px, d):
    return (entry - stop_px) * d > 0 and abs(entry - stop_px) / entry * 100 >= MIN_RISK_PCT


def level_entries(bars, t0, level, d, n15):
    """Entries that use the level directly: at the close, or a limit order part of the way back to it.
    Returns {entry name: (risk %, best R, stopped, final R)}."""
    out = {}
    stop_px = stop_at(level, d)
    close = bars[t0][4]
    if risk_ok(close, stop_px, d):
        out["at close"] = simulate(bars, t0 + 1, close, stop_px, d, TRADE_CANDLES * n15)
    for name, frac in (("limit 50% back", 0.5), ("limit 75% back", 0.25)):
        limit = level + frac * (close - level)
        if not risk_ok(limit, stop_px, d):
            continue
        for j in range(t0 + 1, min(len(bars), t0 + 1 + ENTRY_WAIT * n15)):
            b = bars[j]
            if (d == 1 and b[3] <= limit) or (d == -1 and b[2] >= limit):
                if crossed_stop(b, stop_px, d):  # filled and stopped in the same 15 minutes
                    out[name] = (abs(limit - stop_px) / limit * 100, 0.0, True, -1.0)
                else:
                    out[name] = simulate(bars, j + 1, limit, stop_px, d, TRADE_CANDLES * n15)
                break
    return out


def flip_entry(bars, t0, level, d, lt, n15):
    """First lower-timeframe candle closing with the bands the Dorito's way, if the level hasn't broken."""
    lc, ls, llasts = lt
    stop_px = stop_at(level, d)
    k0 = bisect.bisect_right(llasts, t0 - 1)
    for k1 in range(k0, len(lc)):
        if lc[k1].last > t0 + ENTRY_WAIT * n15:
            return None
        if ls[k1] == d:
            e_idx = lc[k1].last
            if any(crossed_stop(bars[j], stop_px, d) for j in range(t0 + 1, e_idx + 1)):
                return None
            entry = bars[e_idx][4]
            if not risk_ok(entry, stop_px, d):
                return None
            return simulate(bars, e_idx + 1, entry, stop_px, d, TRADE_CANDLES * n15)
    return None


def early_entry(bars, t, level, d, n15):
    """Enter at the close of 15m bar t (half-way through the candle), stop past the level so far."""
    stop_px = stop_at(level, d)
    entry = bars[t][4]
    if not risk_ok(entry, stop_px, d):
        return None
    return simulate(bars, t + 1, entry, stop_px, d, TRADE_CANDLES * n15)


def _trade(info, kind, entry, res):
    risk, best, stopped, final = res
    return {**info, "kind": kind, "ltf": entry, "risk": risk, "best": best, "stopped": stopped, "final": final}


def study_coin(bars, group, oos_from_ms, rnd):
    levels, trades = [], []
    for htf, ltfs in ENTRY_TFS.items():
        hc = aggregate(bars, htf)
        if len(hc) < 60:
            continue
        sigs, lstates = leledc_states(hc, 10, 40)
        hstates = band_states(hc)
        up = HIGHER[htf]
        uc = aggregate(bars, up) if up else []
        ustates = band_states(uc) if up else []
        ulasts = [c.last for c in uc]
        n15 = MIN[htf] // 15
        half = n15 // 2
        lt = {}
        for ltf in ltfs:
            lc = aggregate(bars, ltf)
            lt[ltf] = (lc, band_states(lc), [c.last for c in lc])

        def base_info(i, d, at):
            k = bisect.bisect_right(ulasts, at) - 1
            return {"htf": htf, "dir": d, "group": group, "oos": bars[at][0] >= oos_from_ms,
                    "bands": hstates[i - 1] if i else None, "bands_up": ustates[k] if k >= 0 else None,
                    "given_back": None}

        dorito_bars = {1: [], -1: []}
        for i, s in enumerate(sigs):
            if not s:
                continue
            c = hc[i]
            t0 = c.last
            dorito_bars[s].append(t0)
            level = c.low if s == 1 else c.high
            base = [x.high for x in hc[max(0, i - 10):i + 1]] if s == 1 else [x.low for x in hc[max(0, i - 10):i + 1]]
            run = (max(base) - level) if s == 1 else (level - min(base))
            rng = c.high - c.low
            info = {**base_info(i, s, t0), "bands": hstates[i],
                    "given_back": ((c.close - level) * s / run) if run > 0 else 0.0,
                    "wick": ((min(c.open, c.close) - c.low) if s == 1 else (c.high - max(c.open, c.close))) / rng if rng else 0}
            # 1. Level test
            stop_px = stop_at(level, s)
            broke_at, best = None, 0.0
            for j in range(t0 + 1, min(len(bars), t0 + 1 + LEVEL_CANDLES * n15)):
                _, _, h, l, _ = bars[j]
                if (s == 1 and l < stop_px) or (s == -1 and h > stop_px):
                    broke_at = (j - t0 - 1) // n15 + 1
                    break
                best = max(best, ((h - level) if s == 1 else (level - l)) / level * 100)
            if t0 + LEVEL_CANDLES * n15 < len(bars) or broke_at:
                levels.append({**info, "broke": broke_at, "rotation": best,
                               "close_dist": (c.close - level) * s / level * 100})
            # 2. Trades
            for name, res in level_entries(bars, t0, level, s, n15).items():
                trades.append(_trade(info, "dorito", name, res))
            for ltf in ltfs:
                res = flip_entry(bars, t0, level, s, lt[ltf], n15)
                if res:
                    trades.append(_trade(info, "dorito", f"{ltf} flip", res))

        # 3. Early: Dorito showing half-way through the candle (it may still disappear by the close)
        for i in range(39, len(hc)):
            c = hc[i]
            first, t = c.last - n15 + 1, c.last - n15 + half
            hi = max(bars[j][2] for j in range(first, t + 1))
            lo = min(bars[j][3] for j in range(first, t + 1))
            part = (bars[first][1], hi, lo, bars[t][4])
            prev = hc[i - 39:i]
            s = partial_signal(hc, i, part, lstates[i], max(x.high for x in prev), min(x.low for x in prev), 10, 40)
            if not s:
                continue
            res = early_entry(bars, t, lo if s == 1 else hi, s, n15)
            if res:
                trades.append(_trade({**base_info(i, s, t), "confirmed": sigs[i] == s}, "dorito", "early (half-way)", res))

        # "No Dorito" baseline: the same entries at random candles with no Dorito near, using the swing
        # high/low of the last 3 candles as the level
        for s in (1, -1):
            near = dorito_bars[s]
            idx = [i for i in range(40, len(hc) - 1) if not _near_dorito(near, hc[i].last, (ENTRY_WAIT + 2) * n15)]
            for i in rnd.sample(idx, min(CONTROL_PER_COIN, len(idx))):
                t0 = hc[i].last
                seg = bars[max(0, t0 - 3 * n15 + 1):t0 + 1]
                level = min(b[3] for b in seg) if s == 1 else max(b[2] for b in seg)
                info = base_info(i, s, t0)
                for name, res in level_entries(bars, t0, level, s, n15).items():
                    trades.append(_trade(info, "control", name, res))
                for ltf in ltfs:
                    res = flip_entry(bars, t0, level, s, lt[ltf], n15)
                    if res:
                        trades.append(_trade(info, "control", f"{ltf} flip", res))
                t = t0 - n15 + half + n15  # half-way through the next candle
                if t < len(bars):
                    seg = bars[max(0, t - 3 * n15 + 1):t + 1]
                    lvl = min(b[3] for b in seg) if s == 1 else max(b[2] for b in seg)
                    res = early_entry(bars, t, lvl, s, n15)
                    if res:
                        trades.append(_trade(info, "control", "early (half-way)", res))
    return levels, trades


def _near_dorito(dorito_idx, idx, window):
    k = bisect.bisect_right(dorito_idx, idx) - 1
    return k >= 0 and idx - dorito_idx[k] <= window


# ---------------------------------------------------------------- stats
def wilson(k, n, z=1.96):
    """95% range for a hit rate (Wilson interval)."""
    if not n:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (mid - half, mid + half)


def mean_ci(xs, z=1.96):
    if len(xs) < 2:
        return (statistics.mean(xs) if xs else 0.0, 0.0)
    return statistics.mean(xs), z * statistics.stdev(xs) / math.sqrt(len(xs))


def pc(x):
    return f"{x:.0%}"


def flag(n):
    return " ⚠️" if n < MIN_N else ""


def level_table(rows, title):
    out = [f"**{title}**", "", "| | Doritos | Level broken within 1 candle | within 3 | within 10 "
           "| Move away from the level before it broke (typical / good quarter) | Price vs level at the close |",
           "|---|---|---|---|---|---|---|"]
    for name, rs in rows:
        n = len(rs)
        if not n:
            continue
        cells = []
        for h in (1, 3, 10):
            k = sum(1 for r in rs if r["broke"] and r["broke"] <= h)
            lo, hi = wilson(k, n)
            cells.append(f"{pc(k / n)} ({pc(lo)}–{pc(hi)})")
        rot = sorted(r["rotation"] for r in rs)
        q3 = rot[int(len(rot) * 0.75)] if rot else 0
        out.append(f"| {name}{flag(n)} | {n} | " + " | ".join(cells)
                   + f" | {statistics.median(rot):.1f}% / {q3:.1f}%+ | {statistics.median(r['close_dist'] for r in rs):.1f}% |")
    return out + [""]


def trade_stats(rs, target=3):
    n = len(rs)
    if not n:
        return None
    hits = {t: sum(1 for r in rs if r["best"] >= t) for t in TARGETS}
    rr = [outcome(r, target) for r in rs]
    m, ci = mean_ci(rr)
    return {"n": n, "hits": hits, "e": m, "ci": ci}


def trade_row(name, rs, control=None, target=3):
    s = trade_stats(rs, target)
    if not s:
        return None
    n = s["n"]
    cells = []
    for t in TARGETS:
        lo, hi = wilson(s["hits"][t], n)
        cells.append(f"{pc(s['hits'][t] / n)} ({pc(lo)}–{pc(hi)})")
    ins = trade_stats([r for r in rs if not r["oos"]], target)
    oos = trade_stats([r for r in rs if r["oos"]], target)
    base = trade_stats(control, target) if control else None
    return (f"| {name}{flag(n)} | {n} | " + " | ".join(cells)
            + f" | **{s['e']:+.2f}R** ± {s['ci']:.2f} | "
            + (f"{ins['e']:+.2f}R" if ins else "–") + " / " + (f"{oos['e']:+.2f}R ({oos['n']})" if oos else "–")
            + " | " + (f"{base['e']:+.2f}R" if base else "–") + " |")


TRADE_HEAD = ["| | Trades | Reached 2R before stop | 3R | 5R | Avg result with a 3R target (± range) "
              "| First 2 years / last year | No-Dorito baseline |", "|---|---|---|---|---|---|---|---|"]


def passes(rs, control, target=3):
    """The bar a setup has to clear: enough trades, average clearly above 0 after fees, still positive on the
    last year, and better than the no-Dorito baseline."""
    s = trade_stats(rs, target)
    if not s or s["n"] < MIN_N or s["e"] - s["ci"] <= 0:
        return False
    oos = trade_stats([r for r in rs if r["oos"]], target)
    base = trade_stats(control, target)
    return bool(oos and oos["n"] >= 30 and oos["e"] > 0 and (not base or s["e"] > base["e"]))


def band_name(v):
    return {1: "green", -1: "red"}.get(v, "n/a")


GROUPS = ("BTC", "Big alts", "Other alts", "Small alts")
LEVEL_ENTRIES = ("at close", "limit 50% back", "limit 75% back", "early (half-way)")


def avg_r(s):
    return f"{s['e']:+.2f}R" if s else "–"


def entries_for(htf):
    return list(LEVEL_ENTRIES) + [f"{ltf} flip" for ltf in ENTRY_TFS[htf]]


def splits(htf, entry):
    out = [("All", lambda t: True)]
    out += [(g, (lambda g: lambda t: t["group"] == g)(g)) for g in GROUPS]
    if HIGHER[htf]:
        out += [(f"Bands above ({LABEL[HIGHER[htf]]}) {band_name(b)}", (lambda b: lambda t: t["bands_up"] == b)(b))
                for b in (1, -1)]
    if entry != "early (half-way)":
        out += [("Given back <30%", lambda t: t["given_back"] is not None and t["given_back"] < 0.3),
                ("Given back 60%+ (quick wick)", lambda t: t["given_back"] is not None and t["given_back"] >= 0.6)]
    return out


def report(levels, trades, coins, years, oos_from):
    dname = {1: "Bottoms", -1: "Tops"}
    by = {}
    for t in trades:
        by.setdefault((t["kind"], t["htf"], t["dir"], t["ltf"]), []).append(t)
    out = [
        "# Dorito study: invalidation levels and entries",
        "",
        f"{coins} coins, about {years:.1f} years of Binance 15-minute candles, generated "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. \"Last year\" = from {oos_from}.",
        "",
        "**Entries tested** (stop always just past the Dorito level, "
        f"{BUFFER:.1%} buffer; fees of {FEES_PCT:.2f}% a trade taken off; still open after {TRADE_CANDLES} candles = closed):",
        "- **at close**: buy/sell when the Dorito candle closes.",
        f"- **limit 50% back / 75% back**: after the close, a limit order half / three-quarters of the way back to "
        f"the level, live for {ENTRY_WAIT} candles. Only counts if it fills.",
        "- **early (half-way)**: the Dorito is showing half-way through the candle: enter then, stop past the "
        "candle's low/high so far. Includes the ones that then disappear before the close.",
        f"- **15m flip etc.**: first lower-timeframe candle (within {ENTRY_WAIT} candles) closing with the XO bands "
        "the Dorito's way.",
        "",
        "**No-Dorito baseline**: the same entries at random candles with no Dorito nearby, using the swing low/high "
        "of the last 3 candles as the level. If a Dorito entry doesn't beat its baseline, the Dorito isn't adding "
        "anything.",
        "",
        f"**How to read it.** Brackets / ± = the 95% range. ⚠️ = fewer than {MIN_N} trades, don't lean on it. "
        "**Avg R** = average result per trade with a 3R target. Break-even with a 3R target needs about 25% of "
        "trades to reach 3R.",
        "",
    ]
    tested, winners = 0, []
    for htf in ENTRY_TFS:
        for entry in entries_for(htf):
            for d in (1, -1):
                ctrl = by.get(("control", htf, d, entry), [])
                base = by.get(("dorito", htf, d, entry), [])
                for name, fn in splits(htf, entry):
                    tested += 1
                    rs = [t for t in base if fn(t)]
                    if passes(rs, ctrl):
                        s = trade_stats(rs)
                        winners.append((s["e"], f"{LABEL[htf]} {dname[d].lower()}, {entry}, {name}", s["n"]))
    out += ["## Setups that pass every check", "",
            f"Checked {tested} combinations. To pass: {MIN_N}+ trades, average result above 0 even at the low end "
            "of its range, still positive on the last year alone, and better than the no-Dorito baseline. With this "
            "many combinations a few could pass by luck, so treat a lone pass with care.", ""]
    out += ([f"- **{n}**: {e:+.2f}R a trade, {k} trades" for e, n, k in sorted(winners, reverse=True)]
            or ["**None.** Nothing cleared the bar."])
    out += ["", "## Summary: every entry, all coins", "",
            "| Dorito | Entry | Trades | Reached 2R | Reached 3R | Avg R (± range) | First 2 years / last year | No-Dorito baseline |",
            "|---|---|---|---|---|---|---|---|"]
    for htf in ENTRY_TFS:
        for d in (1, -1):
            for entry in entries_for(htf):
                rs = by.get(("dorito", htf, d, entry), [])
                s = trade_stats(rs)
                if not s:
                    continue
                ins = trade_stats([r for r in rs if not r["oos"]])
                oos = trade_stats([r for r in rs if r["oos"]])
                b = trade_stats(by.get(("control", htf, d, entry), []))
                out.append(f"| {LABEL[htf]} {dname[d].lower()} | {entry} | {s['n']}{flag(s['n'])} "
                           f"| {pc(s['hits'][2] / s['n'])} | {pc(s['hits'][3] / s['n'])} "
                           f"| **{s['e']:+.2f}R** ± {s['ci']:.2f} "
                           f"| {avg_r(ins)} / {avg_r(oos)} | {avg_r(b)} |")
    out.append("")

    for htf in ENTRY_TFS:
        out += [f"## {LABEL[htf]} Doritos", ""]
        for d in (1, -1):
            lv = [r for r in levels if r["htf"] == htf and r["dir"] == d]
            out += [f"### {dname[d]}", ""]
            if lv:
                out += level_table(
                    [("All", lv)] + [(g, [r for r in lv if r["group"] == g]) for g in GROUPS]
                    + [(f"Bands above ({LABEL[HIGHER[htf]]}) {band_name(b)}", [r for r in lv if r["bands_up"] == b])
                       for b in (1, -1) if HIGHER[htf]]
                    + [("Given back <30%", [r for r in lv if r["given_back"] < 0.3]),
                       ("Given back 30–60%", [r for r in lv if 0.3 <= r["given_back"] < 0.6]),
                       ("Given back 60%+ (quick wick)", [r for r in lv if r["given_back"] >= 0.6])],
                    "Does the level hold?")
            for entry in entries_for(htf):
                tr = by.get(("dorito", htf, d, entry), [])
                if not tr:
                    continue
                ctrl = by.get(("control", htf, d, entry), [])
                out += [f"**Entry: {entry}** (typical stop distance {statistics.median(t['risk'] for t in tr):.1f}%)", ""]
                out += TRADE_HEAD
                for name, fn in splits(htf, entry):
                    row = trade_row(name, [t for t in tr if fn(t)], ctrl if name == "All" else None)
                    if row:
                        out.append(row)
                if entry == "early (half-way)":
                    kept = [t for t in tr if t.get("confirmed")]
                    out.append(f"\nOf these, {pc(len(kept) / len(tr))} were still there at the close.")
                out.append("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- data
def load_15m(http, archive, a, symbol, now, months, throttle):
    """~`months` months of 15m candles: Binance's monthly archive files, then the live API for recent weeks."""
    got = {}
    y, m = now.year, now.month
    wanted = []
    for _ in range(months):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        wanted.append((y, m))

    def month(ym):
        url = f"{archive.base}/data/futures/um/monthly/klines/{symbol}/15m/{symbol}-15m-{ym[0]:04d}-{ym[1]:02d}.zip"
        try:
            z = archive._zip_csv(url)
        except Exception as e:  # noqa: BLE001
            log.warning("%s %s: %s", symbol, ym, e)
            return []
        rows = []
        for r in (z[1] if z else []):
            try:
                ts = int(r[0])
                if ts > 1e14:
                    ts //= 1000
                rows.append((ts, float(r[1]), float(r[2]), float(r[3]), float(r[4])))
            except (ValueError, IndexError):
                continue
        return rows

    for rows in parallel_map(month, wanted, 6).values():
        for r in rows:
            got[r[0]] = r
    for b in binance_web_klines(http, a["binance_web_url"], symbol, "15m", pages=6, throttle=throttle):
        ts = int(b.day.timestamp() * 1000)
        got[ts] = (ts, b.open, b.high, b.low, b.close)
    now_ms = int(now.timestamp() * 1000)
    return [got[k] for k in sorted(got) if k + M15 <= now_ms]


def main(argv=None):
    p = argparse.ArgumentParser(description="Dorito invalidation / entry study")
    p.add_argument("--coins", type=int, default=100)
    p.add_argument("--months", type=int, default=37)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    a, d = cfg["alerts"], cfg["data"]
    cfg["alerts"]["top_n"] = args.coins
    http = Http(timeout=d["timeout_seconds"], pool=16)
    coins = pick_coins(cfg, http, hyperliquid_coins(http, d["hyperliquid_url"]))
    archive = BinanceArchive(http, d["binance_archive_url"])
    throttle = Throttle(a["binance_seconds_between_calls"])
    now = datetime.now(timezone.utc)
    oos_from = now - timedelta(days=OOS_DAYS)
    oos_ms = int(oos_from.timestamp() * 1000)
    rnd = random.Random(7)
    levels, trades, spans, used = [], [], [], 0
    for rank, (base, _, symbol) in enumerate(coins):
        group = "BTC" if base == "BTC" else ("Big alts" if rank < 10 else "Other alts" if rank < 50 else "Small alts")
        try:
            bars = load_15m(http, archive, a, symbol, now, args.months, throttle)
        except Exception as e:  # noqa: BLE001
            log.warning("No candles for %s: %s", symbol, e)
            continue
        if len(bars) < 96 * 120:
            log.info("%s: only %d candles, skipped", symbol, len(bars))
            continue
        used += 1
        spans.append((bars[-1][0] - bars[0][0]) / 86_400_000 / 365)
        lv, tr = study_coin(bars, group, oos_ms, rnd)
        levels += lv
        trades += tr
        log.info("%s (%s): %d days, %d Doritos, %d trades", base, group, (bars[-1][0] - bars[0][0]) // 86_400_000,
                 len(lv), sum(t["kind"] == "dorito" for t in tr))
    text = report(levels, trades, used, statistics.median(spans) if spans else 0, f"{oos_from:%d %b %Y}")
    (ROOT / "latest").mkdir(exist_ok=True)
    (ROOT / "latest" / "dorito_study.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
