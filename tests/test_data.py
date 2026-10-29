"""Parsing of recorded sample data from each source."""
import json
from datetime import date

from screener.data import BinanceArchive, bybit_klines, hyperliquid_coins
from tests.conftest import DATA


class FakeResponse:
    def __init__(self, content=b"", payload=None):
        self.content = content
        self._payload = payload

    def json(self):
        return self._payload


class FakeHttp:
    """Serves recorded files instead of the internet."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, ok404=False, **kw):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                return value
        if ok404:
            return None
        raise AssertionError(f"unexpected url {url}")


def test_archive_monthly_klines():
    http = FakeHttp({"1000PEPEUSDT-1d-2026-09.zip": FakeResponse((DATA / "PEPE-1d-2026-09.zip").read_bytes())})
    bars = BinanceArchive(http, "https://data.binance.vision").monthly_klines("1000PEPEUSDT", 2026, 9)
    assert len(bars) == 30
    assert bars[0].day == date(2026, 9, 1) and bars[-1].day == date(2026, 9, 30)
    assert bars[0].close == 0.0034752 and bars[0].quote_volume == 181110118.3827024


def test_archive_klines_walks_back_to_listing():
    http = FakeHttp({"1000PEPEUSDT-1d-2026-09.zip": FakeResponse((DATA / "PEPE-1d-2026-09.zip").read_bytes())})
    bars = BinanceArchive(http, "https://data.binance.vision").klines("1000PEPEUSDT", date(2026, 10, 3), 6)
    assert len(bars) == 30
    # Oct daily files (missing), Sep monthly (found), Aug monthly (missing) -> stop
    assert not any("2026-07" in u for u in http.calls)


def test_archive_metrics():
    http = FakeHttp({"metrics-2026-10-27.zip": FakeResponse((DATA / "PEPE-metrics-2026-10-27.zip").read_bytes())})
    p = BinanceArchive(http, "https://data.binance.vision").metrics_day("1000PEPEUSDT", date(2026, 10, 27))
    assert p.contracts == 19663221320.0 and round(p.value_usd) == 89973592


def test_archive_open_interest_change():
    blob = (DATA / "PEPE-metrics-2026-10-27.zip").read_bytes()
    http = FakeHttp({"metrics-2026-10-27.zip": FakeResponse(blob), "metrics-2026-10-20.zip": FakeResponse(blob)})
    now, before = BinanceArchive(http, "x").open_interest("1000PEPEUSDT", date(2026, 10, 28), [3, 7])
    assert now.day == date(2026, 10, 27) and before[7].day == date(2026, 10, 20)
    assert before[3] is None  # 24th/23rd not recorded in this test


def test_archive_listing():
    page = (DATA / "archive_listing.xml").read_text()
    http = FakeHttp({"amazonaws": FakeResponse(payload=None)})
    http.routes["amazonaws"].text = page
    syms = BinanceArchive(http, "x").list_perp_symbols()
    assert syms == ["1000PEPEUSDT", "BTCUSDT", "DOGEUSDT"]


def test_hyperliquid():
    payload = json.loads((DATA / "hyperliquid_metaAndAssetCtxs.json").read_text())
    coins = hyperliquid_coins(FakeHttp({"hyperliquid": FakeResponse(payload=payload)}), "https://api.hyperliquid.xyz/info")
    assert "kPEPE" in coins and "BTC" in coins
    btc = coins["BTC"]
    assert btc.volume_24h > 1e8 and btc.oi_usd > 1e8
    assert coins["MATIC"].delisted and not coins["BTC"].delisted


def test_hyperliquid_names_match_binance():
    """Recorded Hyperliquid names against real Binance symbols."""
    from screener.universe import match_hyperliquid

    payload = json.loads((DATA / "hyperliquid_metaAndAssetCtxs.json").read_text())
    names = [a["name"] for a in payload[0]["universe"]]
    m = match_hyperliquid(["1000PEPE", "1000BONK", "DOGE", "WIF", "NEIRO", "1000000MOG", "XYZ"], names)
    assert m["1000PEPE"] == "kPEPE" and m["1000BONK"] == "kBONK" and m["NEIRO"] == "kNEIRO"
    assert "XYZ" not in m


def test_bybit():
    # Hand-written in Bybit's documented v5 format: Bybit blocks US servers, so it can't be recorded from GitHub
    payload = json.loads((DATA / "bybit_kline.json").read_text())
    bars = bybit_klines(FakeHttp({"bybit": FakeResponse(payload=payload)}), "https://api.bybit.com", "BTCUSDT")
    assert len(bars) >= 2
    assert bars == sorted(bars, key=lambda b: b.day)
    assert all(b.quote_volume > 0 for b in bars)
