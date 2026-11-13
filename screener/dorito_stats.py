"""Dorito stats: how often a Dorito that is showing part-way through a candle is still there at the close,
and whether getting in early beats waiting for the close.

Each candle (4h / 12h / daily / 3-day / weekly) is rebuilt from 1-hour candles and replayed hour by hour:
at every hour we ask "if the candle closed right now, would there be a Dorito?". Run it on GitHub
(Actions -> Dorito Stats -> Run workflow); the report lands in latest/dorito_stats.md.
"""
import argparse
import logging
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .alerts import pick_coins
from .config import ROOT, load_config
from .data import Http, Throttle, binance_web_klines, hyperliquid_coins

log = logging.getLogger("dorito_stats")
TF_HOURS = {"4h": 4, "12h": 12, "1d": 24, "3d": 72, "1w": 168}
TF_LABEL = {"4h": "4h", "12h": "12h", "1d": "Daily", "3d": "3-day", "1w": "Weekly"}
CHECKPOINTS = (0.25, 0.5, 0.75, 0.9)
HOLD_CANDLES = 3  # how many candles after the Dorito candle to measure the move


@dataclass
class Candle:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    partials: list  # (open, high, low, close) after each hour, last one = the finished candle


def group_key(tf, t):
    if tf in ("4h", "12h"):
        step = TF_HOURS[tf]
        return t.replace(hour=t.hour - t.hour % step, minute=0, second=0, microsecond=0)
    day = t.date()
    if tf == "1d":
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    if tf == "3d":  # same blocks as TradingView's 3D chart (and alerts.to_3day)
        days = (day - date(1970, 1, 1)).days
        d0 = date(1970, 1, 1) + timedelta(days=days - days % 3)
        return datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc)
    d0 = day - timedelta(days=day.weekday())  # weekly: Monday
    return datetime(d0.year, d0.month, d0.day, tzinfo=timezone.utc)


def build_candles(hourly, tf):
    """Candles of this timeframe from 1h bars (keyed by start datetime). Only complete candles (every hour
    present) are kept; a gap ends the run so the indicator never spans missing data."""
    n = TF_HOURS[tf]
    groups = {}
    for b in hourly:
        groups.setdefault(group_key(tf, b.day), []).append(b)
    out = []
    for k in sorted(groups):
        g = sorted(groups[k], key=lambda b: b.day)
        if len(g) != n:
            continue
        partials, hi, lo = [], -math.inf, math.inf
        for b in g:
            hi, lo = max(hi, b.high), min(lo, b.low)
            partials.append((g[0].open, hi, lo, b.close))
        out.append(Candle(k, g[0].open, hi, lo, g[-1].close, partials))
    return out


def leledc_states(candles, count, length):
    """Same as alerts.leledc, but also returns the two running counts *before* each candle, so a candle
    that's still forming can be judged on its own. Returns (signals, states)."""
    up = down = 0
    sigs, states = [], []
    for i, b in enumerate(candles):
        states.append((up, down))
        sig = 0
        if i >= 4:
            if b.close > candles[i - 4].close:
                up += 1
            if b.close < candles[i - 4].close:
                down += 1
        if i >= length - 1:
            window = candles[i - length + 1 : i + 1]
            if up > count and b.close < b.open and b.high >= max(x.high for x in window):
                up = 0
                sig = -1
            elif down > count and b.close > b.open and b.low <= min(x.low for x in window):
                down = 0
                sig = 1
        sigs.append(sig)
    return sigs, states


def partial_signal(candles, i, partial, state, prev_hi, prev_lo, count, length):
    """Dorito on candle i if it closed with this partial (open, high, low, close)."""
    o, h, l, c = partial
    up, down = state
    if i >= 4:
        up += c > candles[i - 4].close
        down += c < candles[i - 4].close
    if i < length - 1:
        return 0
    if up > count and c < o and h >= prev_hi:
        return -1
    if down > count and c > o and l <= prev_lo:
        return 1
    return 0


def replay(candles, count, length):
    """One record per candle where a Dorito showed at any hour or confirmed at the close."""
    sigs, states = leledc_states(candles, count, length)
    out = []
    for i in range(max(4, length - 1), len(candles)):
        prev = candles[i - length + 1 : i]
        prev_hi = max((x.high for x in prev), default=-math.inf)
        prev_lo = min((x.low for x in prev), default=math.inf)
        c = candles[i]
        hourly = [partial_signal(candles, i, p, states[i], prev_hi, prev_lo, count, length) for p in c.partials]
        if not any(hourly):
            continue
        assert hourly[-1] == sigs[i], "replay must agree with the indicator at the close"
        exit_close = candles[i + HOLD_CANDLES].close if i + HOLD_CANDLES < len(candles) else None
        out.append({"i": i, "candle": c, "hourly": hourly, "final": sigs[i], "exit": exit_close})
    return out


def run_length(hourly, k, d):
    """How many hours in a row (ending at hour k, 1-based) the Dorito in direction d has been showing."""
    n = 0
    for s in reversed(hourly[:k]):
        if s != d:
            break
        n += 1
    return n


def pct(a, b):
    return (a / b - 1) * 100


def fmt(v, digits=1, sign=True):
    if v is None:
        return "n/a"
    return f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"


def move(xs):
    """Median move and how often it went your way."""
    if not xs:
        return "n/a"
    return f"{statistics.median(xs):+.1f}% ({sum(x > 0 for x in xs) / len(xs):.0%} in favour)"


def summarise(records, tf):
    """Markdown section for one timeframe."""
    n = TF_HOURS[tf]
    lines = [f"## {TF_LABEL[tf]}", ""]
    for d, name in ((1, "Bottoms (green ▲)"), (-1, "Tops (red ▼)")):
        real = [r for r in records if r["final"] == d]
        lines.append(f"### {name}: {len(real)} confirmed at the close")
        if not real:
            lines.append("")
            continue
        # How long had the confirmed ones been showing by the close?
        held = [run_length(r["hourly"], n, d) / n * 100 for r in real]
        lines.append(f"Confirmed Doritos had been showing (without a break) for a median of "
                     f"**{statistics.median(held):.0f}%** of the candle by the close.")
        lines.append("")
        lines.append("| Showing at | Times showing | Still there at close | Of all real Doritos, caught this early "
                     "| Move left after alert* | Move left if you wait for the close* | Price vs level at alert |")
        lines.append("|---|---|---|---|---|---|---|")
        wait = [d * pct(r["exit"], r["candle"].close) for r in real if r["exit"]]
        for f in CHECKPOINTS:
            k = max(1, math.ceil(f * n))
            streak = max(1, n // 8)
            for min_run, label in ((0, ""), (streak, f", showing {streak}h+ in a row")):
                showing = [r for r in records if r["hourly"][k - 1] == d and run_length(r["hourly"], k, d) >= min_run]
                if not showing:
                    continue
                kept = [r for r in showing if r["final"] == d]
                early = [d * pct(r["exit"], r["candle"].partials[k - 1][3]) for r in showing if r["exit"]]
                wait_same = [d * pct(r["exit"], r["candle"].close) for r in kept if r["exit"]]
                dist = [pct(r["candle"].partials[k - 1][3], r["candle"].partials[k - 1][2 if d == 1 else 1])
                        for r in showing]
                lines.append(f"| {f:.0%} ({k}h){label} | {len(showing)} | **{len(kept) / len(showing):.0%}** "
                             f"| {len(kept) / len(real):.0%} | {move(early)} | {move(wait_same)} "
                             f"| {fmt(statistics.median(dist))} |")
        dist = [pct(r["candle"].close, r["candle"].low if d == 1 else r["candle"].high) for r in real]
        lines.append(f"| **At the close (today's alerts)** | {len(real)} | 100% | 100% | {move(wait)} | – "
                     f"| {fmt(statistics.median(dist))} |")
        lines.append("")
    return lines


def report(all_records, coins, years):
    out = [
        "# Dorito stats: can we get in before the close?",
        "",
        f"{len(coins)} coins (top by Hyperliquid volume), about {years:.1f} years of Binance 1-hour candles, "
        f"generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.",
        "",
        "How to read it: **Showing at 50%** = the Dorito was on the chart halfway through the candle. "
        "**Still there at close** = how often it then confirmed. **Caught this early** = what share of all the "
        "Doritos that confirmed you'd have seen by then.",
        "",
        f"\\* **Move left** = median move in the Dorito's direction (up for bottoms, down for tops) from the alert "
        f"price to the close {HOLD_CANDLES} candles after the Dorito candle, counting the ones that got cancelled. "
        "\"Up\" = how often that move was in your favour. **If you wait for the close** = the same, for the ones "
        "that confirmed, but entering at the close like today's alerts. **Price vs level** = how far price was "
        "from the candle's low (bottoms) or high (tops) at the alert.",
        "",
        "Medians, not averages, so one huge candle doesn't skew it. Past results, not a promise.",
        "",
    ]
    for tf in TF_HOURS:
        recs = [r for c in all_records for r in all_records[c].get(tf, [])]
        out += summarise(recs, tf)
    return "\n".join(out) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Dorito early-signal stats")
    p.add_argument("--coins", type=int, default=50, help="how many coins (top by Hyperliquid volume)")
    p.add_argument("--pages", type=int, default=18, help="pages of 1500 hourly candles per coin (18 ≈ 3 years)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    a, d = cfg["alerts"], cfg["data"]
    cfg["alerts"]["top_n"] = args.coins
    http = Http(timeout=d["timeout_seconds"], pool=4)
    coins = pick_coins(cfg, http, hyperliquid_coins(http, d["hyperliquid_url"]))
    throttle = Throttle(a["binance_seconds_between_calls"])
    now = datetime.now(timezone.utc)
    all_records, spans = {}, []
    for base, _, symbol in coins:
        try:
            hourly = binance_web_klines(http, a["binance_web_url"], symbol, "1h", pages=args.pages, throttle=throttle)
        except Exception as e:  # noqa: BLE001
            log.warning("No 1h candles for %s: %s", symbol, e)
            continue
        hourly = [b for b in hourly if b.day + timedelta(hours=1) <= now]
        if len(hourly) < 24 * 60:
            continue
        spans.append((hourly[-1].day - hourly[0].day).days / 365)
        all_records[base] = {tf: replay(build_candles(hourly, tf), a["leledc_bar_count"], a["leledc_swing_length"])
                             for tf in TF_HOURS}
        log.info("%s: %d hours, %s", base, len(hourly),
                 ", ".join(f"{tf} {sum(r['final'] != 0 for r in v)}" for tf, v in all_records[base].items()))
    text = report(all_records, list(all_records), statistics.median(spans) if spans else 0)
    (ROOT / "latest").mkdir(exist_ok=True)
    (ROOT / "latest" / "dorito_stats.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
