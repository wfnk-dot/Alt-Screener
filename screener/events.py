"""Catalysts channel: the week ahead (high-impact USD news + mega-cap earnings), how BTC reacted to the
same events before, and reminders 1 day, 1 hour and 15 minutes before each one.

Sources (all free, no key, reachable from GitHub): the Forex Factory calendar feed, the Federal
Reserve's FOMC calendar page (for past meeting dates), Nasdaq's earnings calendar, and Binance BTC
daily candles (Hyperliquid as backup) for the reactions.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import ROOT, load_config
from .data import Http, binance_web_klines, hyperliquid_candles
from .gate import last_slot
from .image import AMBER, GREEN, MUTED, RED, TEXT, render_card, tint
from .output import post_discord, week_label, week_of

log = logging.getLogger("events")
STATE = ROOT / "events_state.json"
HISTORY = ROOT / "events_history.json"
ET = ZoneInfo("America/New_York")
BROWSER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/html",
}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


# ---------------------------------------------------------------- sources


def ff_events(http, url, countries, impacts):
    """This week's calendar from Forex Factory's feed: [{title, when (UTC), forecast, previous, country}]."""
    rows = http.request("GET", url, headers=BROWSER).json()
    out = []
    for r in rows:
        if r.get("country") not in countries or r.get("impact") not in impacts:
            continue
        try:
            when = datetime.fromisoformat(r["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        out.append({"title": r["title"], "when": when, "forecast": r.get("forecast") or "",
                    "previous": r.get("previous") or "", "country": r["country"]})
    return out


def tv_events(http, url, start, end, countries, include, skip):
    """TradingView's economic calendar (has future AND past releases): high-importance events whose title
    contains one of `include` (and none of `skip`). Same shape as ff_events."""
    r = http.request("GET", url, headers={**BROWSER, "Origin": "https://www.tradingview.com"},
                     params={"from": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "to": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                             "countries": ",".join(countries), "minImportance": 1}).json()
    out = []
    for e in r.get("result") or []:
        title = e.get("title", "")
        if not any(w.lower() in title.lower() for w in include) or any(w.lower() in title.lower() for w in skip):
            continue
        try:
            when = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue

        def fmt(v):
            if v is None:
                return ""
            return f"{v:g}{e.get('unit') or ''}{e.get('scale') or ''}"

        out.append({"title": title, "when": when, "forecast": fmt(e.get("forecast")), "previous": fmt(e.get("previous")),
                    "country": e.get("country", "")})
    return out


def fomc_decisions(html):
    """FOMC rate-decision times (UTC) from the Fed's calendar page: 2pm New York on the meeting's last day."""
    out = []
    for block in re.split(r"<h4><a id=\"\d+\">", html)[1:]:
        year = re.match(r"(\d{4}) FOMC Meetings", block)
        if not year:
            continue
        y = int(year.group(1))
        for month, days in re.findall(
                r"fomc-meeting__month[^>]*><strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<", block, re.S):
            if "notation" in days.lower() or "unscheduled" in days.lower():
                continue
            nums = re.findall(r"\d+", days)
            if not nums:
                continue
            last_month = month.split("/")[-1].strip().lower()
            if last_month[:3] not in {m[:3] for m in MONTHS}:
                continue
            mnum = next(v for k, v in MONTHS.items() if k.startswith(last_month[:3]))
            try:
                local = datetime(y, mnum, int(nums[-1]), 14, 0, tzinfo=ET)
            except ValueError:
                continue
            out.append(local.astimezone(timezone.utc))
    return sorted(set(out))


def mega_earnings(http, url, day, companies):
    """Earnings on `day` for the companies in `companies` ({ticker: name})."""
    r = http.request("GET", url, params={"date": day.isoformat()}, headers=BROWSER).json()
    rows = ((r or {}).get("data") or {}).get("rows") or []
    out = []
    for row in rows:
        if row.get("symbol") not in companies:
            continue
        slot = row.get("time", "")
        if "pre" in slot:
            when, label = datetime(day.year, day.month, day.day, 8, 0, tzinfo=ET), "before the open"
        elif "after" in slot:
            when, label = datetime(day.year, day.month, day.day, 16, 5, tzinfo=ET), "after the close"
        else:
            when, label = None, "time not set"
        out.append({"title": f"{row['symbol']} earnings", "company": companies[row["symbol"]], "symbol": row["symbol"],
                    "when": when.astimezone(timezone.utc) if when else None, "day": day,
                    "slot": label, "forecast": row.get("epsForecast", ""), "previous": row.get("lastYearEPS", "")})
    return out


def btc_daily(http, cfg):
    """BTC daily candles {UTC date: Bar}: Binance, else Hyperliquid."""
    try:
        bars = binance_web_klines(http, cfg["alerts"]["binance_web_url"], cfg["btc"]["binance_symbol"], "1d", pages=2)
        if bars:
            return {b.day: b for b in bars}
    except Exception as e:  # noqa: BLE001
        log.warning("Binance BTC candles failed, using Hyperliquid: %s", e)
    now_ms = int(time.time() * 1000)
    bars = hyperliquid_candles(http, cfg["data"]["hyperliquid_url"], cfg["btc"]["hyperliquid_coin"],
                               now_ms - 3000 * 86_400_000, now_ms, "1d")
    return {b.day: b for b in bars}


# ---------------------------------------------------------------- grouping and history


def group_of(title, groups):
    for name, titles in groups.items():
        if title in titles:
            return name
    return None


def merge_events(events, groups):
    """One row per group per time slot (e.g. Fed Funds Rate + FOMC Statement = 'FOMC')."""
    merged = {}
    for e in events:
        g = group_of(e["title"], groups)
        key = (g or e["title"], e["when"].date() if g else e["when"])
        if key not in merged:
            merged[key] = {**e, "name": g or e["title"], "group": g, "titles": [e["title"]], "kind": "macro"}
        else:
            m = merged[key]
            m["titles"].append(e["title"])
            if e["when"] < m["when"]:
                m["when"], m["forecast"], m["previous"] = e["when"], e["forecast"], e["previous"]
    for m in merged.values():
        if m["group"] == "FOMC" and any("Projections" in t for t in m["titles"]):
            m["name"] = "FOMC + dot plot"
    return sorted(merged.values(), key=lambda e: e["when"])


def update_history(history, events, fomc, now, groups):
    """Remember when each tracked event happened, so its BTC reaction can be shown next time."""
    for e in events:
        g = group_of(e["title"], groups)
        if g and e["when"] <= now:
            history.setdefault(g, [])
            iso = e["when"].isoformat()
            if all(datetime.fromisoformat(x).date() != e["when"].date() for x in history[g]):
                history[g].append(iso)
    if fomc:
        history["FOMC"] = sorted({*history.get("FOMC", []), *(t.isoformat() for t in fomc if t <= now)})
    for g in history:
        history[g] = sorted(history[g])
    return history


def up_down(times, btc, before, count):
    """(up days, down days) for BTC on the day, over the last `count` times before `before`."""
    past = [datetime.fromisoformat(x) for x in times if datetime.fromisoformat(x) < before][-count:]
    moves = [m for m in (reaction(btc, t)[0] for t in past) if m is not None]
    return sum(1 for m in moves if m > 0), sum(1 for m in moves if m < 0)


def reaction(btc, when):
    """BTC % move: on the day, 3 days and 7 days later, each vs the close the day before. None if not yet."""
    d = when.date()
    base = btc.get(d - timedelta(days=1))
    if not base or not base.close:
        return None, None, None
    pct = [None if btc.get(d + timedelta(days=k)) is None else (btc[d + timedelta(days=k)].close / base.close - 1) * 100
           for k in (0, 3, 7)]
    return tuple(pct)


def follow_through(day, d3, d7):
    """Faint red if it kept falling (each later move lower), faint green if it kept rising, else None."""
    if None in (day, d3, d7):
        return None
    if day < 0 and d3 < day and d7 < d3:
        return tint(RED)
    if day > 0 and d3 > day and d7 > d3:
        return tint(GREEN)
    return None


def _range_pct(btc, day):
    """High-to-low range of that day (wicks included), as % of the close before."""
    bar, prev = btc.get(day), btc.get(day - timedelta(days=1))
    if not bar or not prev or not prev.close:
        return None
    return (bar.high - bar.low) / prev.close * 100


def day_range(btc, when):
    """(event day's range %, that range ÷ the average range of the earlier weekdays of the same week).
    Wednesday's event is compared with Monday and Tuesday; weekends never count; Monday has nothing to compare."""
    d = when.date()
    rng = _range_pct(btc, d)
    earlier = range(1, min(d.weekday(), 5) + 1)
    before = [r for r in (_range_pct(btc, d - timedelta(days=k)) for k in earlier) if r is not None]
    if rng is None or not before:
        return rng, None
    normal = sum(before) / len(before)
    return rng, (rng / normal if normal else None)


# ---------------------------------------------------------------- pictures and text


def _pct(v):
    if v is None:
        return ("n/a", MUTED)
    return (f"{v:+.1f}%", GREEN if v > 0 else RED if v < 0 else TEXT)


def outlook_cards(macro, earnings, history, btc, cfg, tz, monday, start=None):
    ev = cfg["events"]
    rows = []
    items = [(e["when"], e) for e in macro] + [(e["when"] or datetime.combine(e["day"], datetime.min.time(), timezone.utc), e)
                                               for e in earnings]
    for when, e in sorted(items, key=lambda x: x[0]):
        local = when.astimezone(tz)
        macro_row = "group" in e
        ups, downs = up_down(history.get(e["group"]) or [], btc, when, ev["history_count"]) if macro_row else (0, 0)
        rows.append([
            (f"{local:%a} {local.day} {local:%b}", TEXT),
            (f"{local:%H:%M}" if (macro_row or e["when"]) else "TBC", TEXT),
            (e["name"] if macro_row else f"{e['company']} · {e['slot']}", TEXT),
            ("pill", "Macro", RED) if macro_row else ("pill", "Earnings", AMBER),
            (e["forecast"] or "–", TEXT),
            (e["previous"] or "–", MUTED),
            (f"{ups} up · {downs} down", TEXT) if ups + downs else ("–", MUTED),
        ])
    cols = [("Day", "", "left", True), ("Time", "UK", "left", False), ("Event", "", "left", True),
            ("Type", "", "center", False), ("Forecast", "", "right", False), ("Previous", "", "right", False),
            ("BTC on the day", f"last {ev['history_count']} times", "right", False)]
    sunday = monday + timedelta(days=6)
    first = start if start and start < monday else monday
    title = f"Week ahead · {first:%a} {week_label(first)} – Sun {week_label(sunday)}"
    footer = ["High-impact US news (TradingView economic calendar) and big-tech earnings. "
              "Earnings times are approximate (before the open / after the close).",
              "BTC on the day = how many times BTC closed up or down on that day. "
              "Dot plot = the Fed also publishes its rate forecasts. Reminders: 1 day, 1 hour and 15 minutes before."]
    if not rows:
        rows = [[("–", MUTED), ("", MUTED), ("Nothing big scheduled", TEXT), ("", MUTED), ("", MUTED), ("", MUTED), ("", MUTED)]]
    cards = [render_card(title, "Times in UK time", cols, rows, footer)]

    # How BTC reacted the last few times, for each tracked event happening this week
    groups_this_week = [g for g in ev["history_groups"] if any(e.get("group") == g for e in macro)]
    hist_rows = []
    for g in groups_this_week:
        past = [datetime.fromisoformat(x) for x in history.get(g, [])][-ev["history_count"]:]
        for t in reversed(past):
            day, d3, d7 = reaction(btc, t)
            rng, ratio = day_range(btc, t)
            lt = t.astimezone(tz)
            band = follow_through(day, d3, d7)
            hist_rows.append([(g, TEXT), (f"{lt:%a} {lt.day} {lt:%b %Y}", MUTED),
                              (*_pct(day), band), (*_pct(d3), band), (*_pct(d7), band),
                              ("n/a", MUTED) if rng is None else (f"{rng:.1f}%", TEXT),
                              ("–", MUTED) if ratio is None else (f"{ratio:.1f}x", AMBER if ratio >= ev["range_highlight"] else TEXT)])
        if not past:
            hist_rows.append([(g, TEXT), ("history builds up from now", MUTED)] + [("", MUTED)] * 5)
    if hist_rows:
        cards.append(render_card("How BTC reacted last times", "Moves vs the close the day before",
                                 [("Event", "", "left", True), ("Date", "", "left", False), ("On the", "day", "right", False),
                                  ("After", "3 days", "right", False), ("After", "7 days", "right", False),
                                  ("Day's", "range", "right", False), ("vs earlier", "that week", "right", False)],
                                 hist_rows,
                                 ["Red / green shading = it kept falling / kept rising (day → 3 days → 7 days). "
                                  "Day's range = high to low, wicks included.",
                                  "vs earlier that week = that range divided by the average range of the weekdays before it in the same "
                                  f"week (Wed is compared with Mon-Tue; 2.0x = twice as wild). Yellow = {ev['range_highlight']:g}x or more.",
                                  "Past dates: Federal Reserve (FOMC) and TradingView's economic calendar; new ones are added as they happen."]))
    return cards


def reminder_card(label, when, items, history, btc, tz):
    rows = []
    for e in items:
        last = "–"
        if e.get("group") in history:
            past = [datetime.fromisoformat(x) for x in history[e["group"]] if datetime.fromisoformat(x) < when]
            if past:
                moves = [reaction(btc, t)[0] for t in past[-3:]]
                last = "  ".join(f"{m:+.1f}%" for m in moves if m is not None) or "–"
        if "group" not in e:
            last = "–"
        rows.append([(e["name"] if "group" in e else f"{e['company']} · {e['slot']}", TEXT),
                     ("pill", "Macro", RED) if "group" in e else ("pill", "Earnings", AMBER),
                     (e.get("forecast") or "–", TEXT), (e.get("previous") or "–", MUTED), (last, MUTED)])
    cols = [("Event", "", "left", True), ("Type", "", "center", False), ("Forecast", "", "right", False),
            ("Previous", "", "right", False), ("BTC on the day", "last 3 times", "right", False)]
    local = when.astimezone(tz)
    return render_card(f"{label} · {local:%a} {local.day} {local:%b %H:%M} UK", "", cols, rows)


# ---------------------------------------------------------------- run


def run(cfg, mode, dry_run=False, now=None):
    ev = cfg["events"]
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    now = now or datetime.now(timezone.utc)
    http = Http(timeout=cfg["data"]["timeout_seconds"])
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    url = os.environ.get("DISCORD_NEWS_WEBHOOK_URL")
    if not dry_run and not url:
        log.warning("DISCORD_NEWS_WEBHOOK_URL secret is not set yet (see SETUP.md), so nothing to do")
        return 0

    try:
        raw = tv_events(http, ev["tv_calendar_url"], now - timedelta(days=8), now + timedelta(days=9),
                        ev["tv_countries"], ev["include"], ev.get("skip") or [])
    except Exception as e:  # noqa: BLE001
        log.warning("TradingView calendar failed, using Forex Factory (this week only): %s", e)
        raw = ff_events(http, ev["calendar_url"], ev["countries"], ev["impacts"])
    groups = ev["groups"]
    macro = merge_events(raw, groups)
    fomc = []
    try:
        fomc = fomc_decisions(http.request("GET", ev["fomc_calendar_url"], headers=BROWSER).text)
    except Exception as e:  # noqa: BLE001
        log.warning("Fed calendar unavailable: %s", e)
    history = update_history(history, raw, fomc, now, groups)
    btc = btc_daily(http, cfg)

    # Earnings for the coming 7 days (state keeps what's already known, so reminders work all week)
    earnings = []
    for k in range(8):
        day = (now + timedelta(days=k)).date()
        if day.weekday() >= 5:
            continue
        try:
            earnings += mega_earnings(http, ev["earnings_url"], day, ev["earnings_companies"])
        except Exception as e:  # noqa: BLE001
            log.warning("Earnings calendar failed for %s: %s", day, e)

    posts = []
    sent = set(state.get("sent", []))
    if mode == "outlook":
        monday = week_of(now.astimezone(tz).date())
        week_end = datetime.combine(monday + timedelta(days=7), datetime.min.time(), tz)
        week_macro = [e for e in macro if now <= e["when"] < week_end]
        week_earn = [e for e in earnings if now.astimezone(tz).date() <= e["day"] < week_end.date()
                     and (e["when"] is None or e["when"] >= now)]
        cards = outlook_cards(week_macro, week_earn, history, btc, cfg, tz, monday, start=now.astimezone(tz).date())
        posts.append(("**Week ahead**", [("week_ahead.png", cards[0], "image/png")]))
        for i, c in enumerate(cards[1:], 1):
            posts.append((None, [(f"week_history_{i}.png", c, "image/png")]))  # own message, no ping
        state["last_outlook"] = now.isoformat()

    # Reminders: everything with a known time in the next 25 hours
    upcoming = [e for e in macro + [x for x in earnings if x["when"]] if now < e["when"] <= now + timedelta(hours=25)]
    later = []
    for minutes, label in [(m, _label(m)) for m in sorted(ev["reminders_minutes"], reverse=True)]:
        for when in sorted({e["when"] for e in upcoming}):
            key = f"{when.isoformat()}|{minutes}"
            if key in sent:
                continue
            lead = when - now
            if minutes >= 120:
                if timedelta(hours=2) < lead <= timedelta(minutes=minutes):
                    items = [e for e in upcoming if e["when"] == when]
                    posts.append(_reminder_post(label, when, items, history, btc, tz))
                    sent.add(key)
            elif lead <= timedelta(minutes=minutes) + timedelta(minutes=ev["reminder_wait_minutes"]):
                later.append((when - timedelta(minutes=minutes), key, label, when))
    sent_now = _send(posts, url, ev, dry_run)
    if not dry_run:
        state["sent"] = sorted(sent)
        _save(state, history)
    # Short-notice reminders: wait in this run until it's time (GitHub's hourly timer can't hit :15)
    for fire_at, key, label, when in sorted(later):
        wait = (fire_at - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            log.info("Waiting %.0f minutes to send the %s reminder", wait / 60, label)
            if not dry_run:
                time.sleep(wait)
        items = [e for e in upcoming if e["when"] == when]
        sent_now += _send([_reminder_post(label, when, items, history, btc, tz)], url, ev, dry_run)
        sent.add(key)
        if not dry_run:
            state["sent"] = sorted(sent)
            _save(state, history)
    return sent_now


def _label(minutes):
    if minutes >= 1440:
        return "Tomorrow" if minutes == 1440 else f"In {minutes // 1440} days"
    if minutes >= 60:
        return "In 1 hour" if minutes == 60 else f"In {minutes // 60} hours"
    return f"In {minutes} minutes"


def _reminder_post(label, when, items, history, btc, tz):
    names = ", ".join(dict.fromkeys(e["name"] if "group" in e else e["company"] + " earnings" for e in items))
    text = f"⏰ **{label}** · {names} at {when.astimezone(tz):%H:%M} UK"
    return text, [("reminder.png", reminder_card(label, when, items, history, btc, tz), "image/png")]


def _send(posts, url, ev, dry_run):
    for text, files in posts:
        ping = (ev.get("ping") or "") if text else ""  # only the first message of a post pings
        content = ((ping + "\n" + text) if ping else text) if text else ""
        if dry_run:
            print(content)
            for name, data, _ in files:
                (ROOT / "latest").mkdir(exist_ok=True)
                (ROOT / "latest" / name).write_bytes(data)
        else:
            post_discord(url, content[:1900], files, ping=ping)
    return len(posts)


def _save(state, history):
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    HISTORY.write_text(json.dumps(history, indent=1, sort_keys=True) + "\n")


def outlook_due(now_local, ev, last_outlook):
    slot = last_slot(now_local, ev["outlook_day"], ev["outlook_time"])
    if now_local - slot >= timedelta(hours=24):
        return False
    return not last_outlook or datetime.fromisoformat(last_outlook) < slot


def main(argv=None):
    p = argparse.ArgumentParser(description="Catalysts: week ahead and reminders")
    p.add_argument("--outlook", action="store_true", help="post the week-ahead outlook now")
    p.add_argument("--scheduled", action="store_true", help="hourly run: outlook if it's time, plus reminders")
    p.add_argument("--dry-run", action="store_true", help="print instead of posting; pictures go to latest/")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    mode = "outlook" if args.outlook else "reminders"
    if args.scheduled:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        now_local = datetime.now(ZoneInfo(cfg["schedule"]["timezone"]))
        if outlook_due(now_local, cfg["events"], state.get("last_outlook")):
            mode = "outlook"
    try:
        run(cfg, mode, dry_run=args.dry_run)
    except Exception as e:
        log.exception("Catalysts failed")
        (ROOT / "run_error.txt").write_text(f"Catalysts: {type(e).__name__}: {e}"[:300], encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

