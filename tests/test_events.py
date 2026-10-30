import json
from datetime import date, datetime, timedelta, timezone

from screener.events import fomc_decisions, ff_events, group_of, mega_earnings, merge_events, reaction, update_history
from tests.conftest import DATA
from tests.test_data import FakeHttp, FakeResponse

GROUPS = {"FOMC": ["Federal Funds Rate", "FOMC Statement", "FOMC Press Conference"], "PCE": ["Core PCE Price Index m/m"]}


def ff():
    payload = json.loads((DATA / "ff_calendar_thisweek.json").read_text())
    return ff_events(FakeHttp({"faireconomy": FakeResponse(payload=payload)}), "https://nfs.faireconomy.media/x", ["USD"], ["High"])


def test_forex_factory_red_usd_only():
    events = ff()
    assert events and all(e["country"] == "USD" for e in events)
    titles = {e["title"] for e in events}
    assert {"Federal Funds Rate", "FOMC Statement", "Core PCE Price Index m/m"} <= titles
    fed = next(e for e in events if e["title"] == "Federal Funds Rate")
    assert fed["when"] == datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)  # 2pm New York (EDT)


def test_merge_fomc_into_one_row():
    rows = merge_events(ff(), GROUPS)
    fomc = [r for r in rows if r["name"] == "FOMC"]
    assert len(fomc) == 1 and len(fomc[0]["titles"]) == 3
    assert fomc[0]["when"] == datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)
    assert group_of("CPI m/m", {"CPI": ["CPI m/m"]}) == "CPI" and group_of("Other", GROUPS) is None


def test_fomc_dates_from_fed_page():
    times = fomc_decisions((DATA / "fomc_calendar.html").read_text())
    days = {t.date() for t in times}
    assert date(2026, 10, 28) in days and date(2026, 1, 28) in days and date(2025, 12, 10) in days
    assert date(2026, 10, 27) not in days  # first day of the meeting isn't the decision
    jan = next(t for t in times if t.date() == date(2026, 1, 28))
    assert jan.hour == 19  # 2pm New York in winter = 19:00 UTC


def test_big_tech_earnings_only():
    payload = json.loads((DATA / "nasdaq_earnings_2026-10-29.json").read_text())
    companies = {"AMZN": "Amazon", "AAPL": "Apple"}
    rows = mega_earnings(FakeHttp({"nasdaq": FakeResponse(payload=payload)}), "https://api.nasdaq.com/x",
                         date(2026, 10, 29), companies)
    assert [r["symbol"] for r in rows] == ["AMZN"]  # Exxon, AbbVie etc. are left out
    amzn = rows[0]
    assert amzn["company"] == "Amazon" and amzn["slot"] == "after the close"
    assert amzn["when"] == datetime(2026, 10, 29, 20, 5, tzinfo=timezone.utc)


def test_tradingview_calendar(cfg):
    from screener.events import tv_events

    payload = json.loads((DATA / "tv_calendar.json").read_text())
    ev = cfg["events"]
    rows = tv_events(FakeHttp({"tradingview": FakeResponse(payload=payload)}), "https://economic-calendar.tradingview.com/x",
                     datetime(2026, 9, 25, tzinfo=timezone.utc), datetime(2026, 10, 30, tzinfo=timezone.utc),
                     ["US"], ev["include"], ev["skip"])
    titles = {r["title"] for r in rows}
    assert {"Fed Interest Rate Decision", "Inflation Rate MoM", "Non Farm Payrolls"} <= titles
    assert not any("Housing" in t or "Existing Home" in t for t in titles)  # not on the list
    cpi = next(r for r in rows if r["title"] == "Inflation Rate MoM" and r["when"].date() == date(2026, 10, 14))
    assert cpi["forecast"] == "0.6%" and cpi["previous"] == "0.4%"
    nfp = next(r for r in rows if r["title"] == "Non Farm Payrolls" and r["when"].date() == date(2026, 10, 2))
    assert nfp["forecast"] == "90K"
    merged = merge_events(rows, ev["groups"])
    assert sum(1 for m in merged if m["name"] == "FOMC" and m["when"].date() == date(2026, 10, 28)) == 1


def _bar(d, close, high=None, low=None):
    from screener.data import Bar

    return Bar(d, close, high if high is not None else close, low if low is not None else close, close, 0)


def test_btc_reaction():
    d = date(2026, 10, 28)
    btc = {d - timedelta(days=1): _bar(d - timedelta(days=1), 100.0), d: _bar(d, 102.0),
           d + timedelta(days=3): _bar(d + timedelta(days=3), 95.0), d + timedelta(days=7): _bar(d + timedelta(days=7), 110.0)}
    day, d3, d7 = reaction(btc, datetime(2026, 10, 28, 18, tzinfo=timezone.utc))
    assert round(day, 6) == 2.0 and round(d3, 6) == -5.0 and round(d7, 6) == 10.0
    assert reaction({d - timedelta(days=1): _bar(d, 100.0), d: _bar(d, 101.0)}, datetime(2026, 10, 28, 18, tzinfo=timezone.utc))[1] is None


def test_day_range_vs_days_before():
    from screener.events import day_range

    d = date(2026, 11, 6)
    btc = {}
    for k in range(7, 0, -1):  # quiet days before: 2% high-low range on a 100 close
        btc[d - timedelta(days=k)] = _bar(d - timedelta(days=k), 100.0, 101.0, 99.0)
    btc[d] = _bar(d, 100.0, 103.0, 97.0)  # event day: 6% range with wicks
    rng, ratio = day_range(btc, datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc), 5)
    assert round(rng, 6) == 6.0 and round(ratio, 6) == 3.0


def test_history_records_past_events_once():
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    h = update_history({}, ff(), [datetime(2026, 9, 16, 18, tzinfo=timezone.utc)], now, GROUPS)
    assert "2026-10-28T18:00:00+00:00" in h["FOMC"] and "2026-09-16T18:00:00+00:00" in h["FOMC"]
    assert len(h["FOMC"]) == 2  # three FOMC titles that day count once
    again = update_history(h, ff(), [], now, GROUPS)
    assert len(again["FOMC"]) == 2 and len(again["PCE"]) == 1


def test_reminder_timing(cfg, monkeypatch, capsys, tmp_path):
    from screener import events

    now = datetime(2026, 11, 3, 12, 40, tzinfo=timezone.utc)
    soon = {"title": "CPI m/m", "when": now + timedelta(minutes=50), "forecast": "0.3%", "previous": "0.2%", "country": "USD"}
    tomorrow = {"title": "Federal Funds Rate", "when": now + timedelta(hours=20), "forecast": "4.00%", "previous": "4.00%",
                "country": "USD"}
    far = {"title": "PPI m/m", "when": now + timedelta(days=3), "forecast": "", "previous": "", "country": "USD"}

    class NoNet:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(events, "Http", NoNet)
    monkeypatch.setattr(events, "ROOT", tmp_path)
    monkeypatch.setattr(events, "ff_events", lambda *a, **k: [soon, tomorrow, far])
    monkeypatch.setattr(events, "btc_daily", lambda *a, **k: {})
    monkeypatch.setattr(events, "mega_earnings", lambda *a, **k: [])
    monkeypatch.setattr(events, "STATE", DATA / "does-not-exist.json")
    monkeypatch.setattr(events, "HISTORY", DATA / "does-not-exist-either.json")
    sent = events.run(cfg, "reminders", dry_run=True, now=now)
    out = capsys.readouterr().out
    assert sent == 3
    assert "**Tomorrow** · FOMC" in out          # 20 hours out: the day-before reminder
    assert "**In 1 hour** · CPI" in out          # 50 minutes out: 1-hour reminder goes now
    assert "**In 15 minutes** · CPI" in out      # and the 15-minute one waits for its moment
    assert "PPI" not in out                      # 3 days out: nothing yet


def test_dot_plot_fomc_and_up_down():
    from screener.events import up_down

    when = datetime(2026, 12, 9, 19, tzinfo=timezone.utc)
    rows = merge_events([{"title": "Fed Interest Rate Decision", "when": when, "forecast": "", "previous": "", "country": "US"},
                         {"title": "FOMC Economic Projections", "when": when, "forecast": "", "previous": "", "country": "US"}],
                        {"FOMC": ["Fed Interest Rate Decision", "FOMC Economic Projections"]})
    assert [r["name"] for r in rows] == ["FOMC + dot plot"]
    d1, d2 = date(2026, 9, 16), date(2026, 10, 28)
    btc = {d1 - timedelta(days=1): _bar(d1, 100.0), d1: _bar(d1, 103.0), d2 - timedelta(days=1): _bar(d2, 100.0), d2: _bar(d2, 99.0)}
    times = ["2026-09-16T18:00:00+00:00", "2026-10-28T18:00:00+00:00"]
    assert up_down(times, btc, when, 9) == (1, 1)
    assert up_down([], btc, when, 9) == (0, 0)
