from screener.metrics import Coin
from screener.notes import notes_messages, timeframe_note
from screener.tags import tag_coin
from tests.conftest import make_bars, zigzag


def test_note_after_breakout(cfg):
    ind = cfg["indicators"]
    closes = zigzag(80, -0.01, amp=0.0) + [zigzag(80, -0.01, amp=0.0)[-1] * 1.04 ** i for i in range(1, 25)]
    bars = make_bars(closes)
    for b in bars[80:95]:
        b.quote_volume = 3e6  # breakout on 3x volume
    note = timeframe_note(bars, ind, weekly=False)
    assert note.startswith("bands green since")
    assert "broke above the bands" in note and "x normal volume" in note
    assert "fast EMA" in note


def test_no_note_without_history(cfg):
    assert timeframe_note(make_bars([1.0, 2.0, 3.0]), cfg["indicators"], weekly=True) is None


def test_notes_are_spoilers_and_fit_discord(cfg):
    coins = []
    for n in range(10):
        c = Coin(f"C{n}USDT", f"C{n}", f"C{n}", make_bars(zigzag(400, 0.004, amp=0.1, period=30)))
        tag_coin(c, cfg)
        coins.append(c)
    msgs = notes_messages(coins, cfg)
    assert msgs[0].startswith("**Notes**")
    assert all(len(m) <= 2000 for m in msgs)
    body = "\n".join(msgs)
    assert body.count("||") == 20 and "Weekly (" in body and "Daily (" in body
