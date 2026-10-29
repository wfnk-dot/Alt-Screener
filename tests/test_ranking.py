import pytest

from screener.metrics import Coin
from screener.ranking import percentile_ranks, score_and_rank


def test_percentile_ranks():
    assert percentile_ranks([10, 30, 20]) == [0.0, 1.0, 0.5]
    assert percentile_ranks([1, 1, 2]) == [0.25, 0.25, 1.0]
    assert percentile_ranks([5, None, 7]) == [0.0, 0.5, 1.0]
    assert percentile_ranks([3]) == [1.0]


def coin(name, atr, vr, oi_ch, ret, oi):
    c = Coin(name + "USDT", name, name, [])
    c.atr_pct, c.vol_ratio, c.oi_change_pct, c.return_7d_pct, c.oi_usd = atr, vr, oi_ch, ret, oi
    return c


def test_score(cfg):
    w = cfg["ranking_weights"]
    a = coin("A", 10, 2.0, -40, -20, 100e6)  # top on everything (abs values for OI change and return)
    b = coin("B", 5, 1.0, 10, 5, 50e6)
    c = coin("C", 6, 0.5, 5, 1, 20e6)
    ranked = score_and_rank([b, c, a], w)
    assert a.score == pytest.approx(100)
    # B: atr 0, vol 0.5, oi_ch 0.5, ret 0.5, oi 0.5 -> (0*30 + .5*25 + .5*20 + .5*15 + .5*10)
    assert b.score == pytest.approx(35)
    # C: atr .5, vol 0, oi_ch 0, ret 0, oi 0
    assert c.score == pytest.approx(15)
    assert [x.base for x in ranked] == ["A", "B", "C"]


def test_weights_from_config(cfg):
    assert sum(cfg["ranking_weights"].values()) == 100
    w = {"atr_pct": 0, "volume_ratio": 0, "oi_change_abs": 0, "return_7d_abs": 0, "oi_size": 1}
    ranked = score_and_rank([coin("S", 9, 9, 9, 9, 1e6), coin("L", 1, 1, 1, 1, 9e6)], w)
    assert ranked[0].base == "L"
