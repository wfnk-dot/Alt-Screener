import io
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from screener.gate import last_slot, should_run
from screener.metrics import Coin
from PIL import Image

from screener import output
from screener.image import render_table
from screener.output import diff_lists, discord_message, post_discord, watchlist_text, week_label, week_of, week_title
from screener.state import load_state, previous_list, record_week
from screener.tags import tag_coin
from tests.conftest import make_bars, zigzag

UK = ZoneInfo("Europe/London")


def coins(*names):
    out = []
    for n in names:
        c = Coin(n + "USDT", n, n, make_bars(zigzag(200, 0.01)))
        c.atr_pct, c.return_7d_pct, c.oi_change_pct, c.funding_8h_pct = 5.25, 12.0, -8.0, 0.01
        out.append(c)
    return out


def test_tradingview_format(cfg):
    text = watchlist_text(coins("1000PEPE", "WIF"), cfg)
    assert text == (
        "###CORE,BINANCE:BTCUSDT.P,BINANCE:ETHUSDT.P,BINANCE:SOLUSDT.P,BINANCE:HYPEUSDT.P,OANDA:XAUUSD,"
        "###ALTS,BINANCE:1000PEPEUSDT.P,BINANCE:WIFUSDT.P\n"
    )
    assert text.count("\n") == 1 and " " not in text


def test_diff_lists():
    assert diff_lists(["A", "B", "C"], ["B", "D", "C"]) == (["A"], ["D"])
    assert diff_lists(["A"], None) == (["A"], [])
    assert diff_lists(["A"], ["A"]) == ([], [])


def test_state_previous_week():
    s = {"weeks": {}, "last_scheduled_week": None}
    record_week(s, "2026-10-26", ["A", "B"], 8, scheduled=True)
    record_week(s, "2026-11-02", ["B", "C"], 8, scheduled=False)
    assert previous_list(s, "2026-11-02") == ["A", "B"]  # same-week reruns compare to last week
    assert previous_list(s, "2026-11-09") == ["B", "C"]
    assert previous_list(s, "2026-10-26") is None
    assert s["last_scheduled_week"] == "2026-10-26"
    for i in range(10):
        record_week(s, f"2027-01-{i + 1:02d}", ["X"], 3, scheduled=False)
    assert len(s["weeks"]) == 3


def test_load_missing_state(tmp_path):
    assert load_state(tmp_path / "nope.json") == {"weeks": {}, "last_scheduled_week": None}


def test_week_of():
    assert week_of(date(2026, 11, 1)) == date(2026, 11, 2)   # Sunday -> next day
    assert week_of(date(2026, 11, 2)) == date(2026, 11, 2)   # Monday -> itself
    assert week_of(date(2026, 10, 29)) == date(2026, 11, 2)  # Thursday
    assert week_label(date(2026, 11, 2)) == "2 Nov"


def test_discord_message(cfg):
    cs = coins("1000PEPE", "WIF")
    for c in cs:
        tag_coin(c, cfg)
    cs[1].funding_8h_pct = 0.08
    cs[1].tags["funding_extreme"] = True
    msg = discord_message(cs, ["WIF"], ["DOGE"], date(2026, 11, 2), cfg)
    assert msg.startswith("@here\n**Alt watchlist: Mon 2 Nov – Sun 8 Nov**")
    assert "**New this week:** WIF" in msg and "**Dropped off:** DOGE" in msg
    assert "extreme funding" in msg
    assert "Import list" in msg
    cfg["discord"]["ping"] = ""
    assert discord_message(cs, [], [], date(2026, 11, 2), cfg).startswith("**Alt watchlist")


def test_week_title_across_months():
    assert week_title(date(2026, 11, 30)) == "Alt watchlist: Mon 30 Nov – Sun 6 Dec"


def test_post_discord_ping_and_files(monkeypatch):
    sent = {}

    class R:
        status_code = 200

    def fake_post(url, data, files, timeout):
        sent.update(url=url, payload=json.loads(data["payload_json"]), files=files)
        return R()

    monkeypatch.setattr(output.requests, "post", fake_post)
    post_discord("https://discord/hook", "@here hi", [("table.png", b"x", "image/png"), ("watchlist.txt", b"y", "text/plain")], ping="@here")
    assert sent["url"].endswith("?wait=true")
    assert sent["payload"]["allowed_mentions"] == {"parse": ["everyone"]}
    assert list(sent["files"]) == ["files[0]", "files[1]"]
    post_discord("https://discord/hook", "hi", [], ping="")
    assert sent["payload"]["allowed_mentions"] == {"parse": []}


def test_table_image(cfg):
    cs = coins("1000PEPE", "WIF", "ZEC")
    for c in cs:
        tag_coin(c, cfg)
    png = render_table(cs, cfg, title="Alt watchlist: Mon 2 Nov – Sun 8 Nov", subtitle="Prices: test")
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.width > 800 and img.height > 200
    cfg["table"]["columns"] = ["rank", "coin"]
    narrow = Image.open(io.BytesIO(render_table(cs, cfg, title="x")))
    assert narrow.width < img.width


def test_gate_handles_gmt_and_bst():
    sched = {"day": "Sunday", "time": "18:00"}
    # BST (summer): 18:00 UK = 17:00 UTC
    summer = datetime(2026, 7, 5, 17, 7, tzinfo=ZoneInfo("UTC")).astimezone(UK)
    assert summer.hour == 18 and should_run(summer, sched, None)
    assert not should_run(datetime(2026, 7, 5, 16, 7, tzinfo=ZoneInfo("UTC")).astimezone(UK), sched, None)
    # GMT (winter): 18:00 UK = 18:00 UTC
    winter = datetime(2026, 11, 1, 18, 7, tzinfo=ZoneInfo("UTC")).astimezone(UK)
    assert should_run(winter, sched, None)
    assert not should_run(datetime(2026, 11, 1, 17, 7, tzinfo=ZoneInfo("UTC")).astimezone(UK), sched, None)


def test_gate_runs_once_and_catches_up():
    sched = {"day": "Sunday", "time": "18:00"}
    late = datetime(2026, 11, 1, 23, 7, tzinfo=UK)
    assert should_run(late, sched, "2026-10-26")
    assert not should_run(late, sched, "2026-11-02")  # already posted this week
    assert should_run(datetime(2026, 11, 2, 17, 7, tzinfo=UK), sched, None)  # GitHub skipped Sunday evening
    assert not should_run(datetime(2026, 11, 2, 18, 7, tzinfo=UK), sched, None)  # >24h late: give up
    assert not should_run(datetime(2026, 11, 4, 18, 7, tzinfo=UK), sched, None)  # Wednesday


def test_last_slot():
    now = datetime(2026, 11, 1, 17, 59, tzinfo=UK)
    assert last_slot(now, "Sunday", "18:00") == datetime(2026, 10, 25, 18, 0, tzinfo=UK)
