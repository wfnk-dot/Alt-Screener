"""Checks each data source answers from wherever this runs (used to verify GitHub Actions)."""
import sys
from datetime import date, timedelta

from .config import load_config
from .data import BinanceArchive, BinanceLive, Http, bybit_klines, hyperliquid_coins


def main():
    cfg = load_config()
    d = cfg["data"]
    http = Http(timeout=20, retries=1)
    archive = BinanceArchive(http, d["binance_archive_url"])
    checks = {
        "Binance archive listing": lambda: f"{len(archive.list_perp_symbols())} symbols",
        "Binance archive klines": lambda: f"{len(archive.klines('BTCUSDT', date.today(), 1))} daily candles",
        "Binance archive metrics": lambda: str(archive.open_interest("BTCUSDT", date.today() - timedelta(days=1), 7)[0]),
        "Hyperliquid": lambda: f"{len(hyperliquid_coins(http, d['hyperliquid_url']))} coins",
        "Bybit klines": lambda: f"{len(bybit_klines(http, d['bybit_url'], 'BTCUSDT', 30))} candles",
        "Binance live exchangeInfo": lambda: "ok" if BinanceLive(http, d["binance_api_url"]).exchange_info() else "unreachable",
        "Binance live funding": lambda: "ok" if BinanceLive(http, d["binance_api_url"]).funding_8h() else "unreachable",
    }
    for name, fn in checks.items():
        try:
            print(f"OK   {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
