"""Public data sources: Binance archive (data.binance.vision), Hyperliquid, Bybit, Binance live API.

Binance's live futures API blocks US IPs (GitHub's runners are in the US), so the
archive is the main source and the live API is only used when it answers.
"""
import csv
import io
import logging
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

# Listing the archive goes through its S3 bucket API (that's what the archive's own web page does)
ARCHIVE_LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"


@dataclass
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    quote_volume: float


@dataclass
class OIPoint:
    day: date
    contracts: float
    value_usd: float


@dataclass
class Series:
    symbol: str
    bars: list = field(default_factory=list)
    source: str = "Binance archive"


class Http:
    def __init__(self, timeout=30, retries=3, pool=16):
        self.s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
        self.s.mount("https://", adapter)
        self.s.headers["User-Agent"] = "alt-screener (github.com/wfnk-dot/alt-screener)"
        self.timeout = timeout
        self.retries = retries

    def request(self, method, url, ok404=False, **kw):
        last = None
        for attempt in range(self.retries):
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                if r.status_code == 404 and ok404:
                    return None
                if r.status_code in (403, 404, 451):
                    r.raise_for_status()  # blocked/missing: retrying won't help
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"{r.status_code} from {url}", response=r)
                r.raise_for_status()
                return r
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (403, 404, 451):
                    raise
                last = e
            except requests.RequestException as e:
                last = e
            time.sleep(1.5 * (attempt + 1))
        raise last


# ---------------------------------------------------------------- Binance archive


class BinanceArchive:
    def __init__(self, http, base_url, list_url=ARCHIVE_LIST_URL):
        self.http = http
        # Override lets a local build use the S3 mirror; GitHub uses data.binance.vision
        self.base = os.environ.get("ALT_SCREENER_ARCHIVE_URL", base_url).rstrip("/")
        self.list_url = list_url

    def list_perp_symbols(self):
        """Every symbol that has a futures/um daily klines folder (includes delisted ones)."""
        prefix = "data/futures/um/daily/klines/"
        symbols, marker = [], ""
        while True:
            r = self.http.request(
                "GET", self.list_url, params={"delimiter": "/", "prefix": prefix, "marker": marker}
            )
            found = re.findall(r"<Prefix>" + re.escape(prefix) + r"([^/<]+)/</Prefix>", r.text)
            symbols += found
            nxt = re.search(r"<NextMarker>([^<]+)</NextMarker>", r.text)
            if "<IsTruncated>true</IsTruncated>" not in r.text or not nxt:
                break
            marker = nxt.group(1)
        return sorted(set(symbols))

    def _zip_csv(self, url):
        r = self.http.request("GET", url, ok404=True)
        if r is None:
            return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            text = z.read(z.namelist()[0]).decode()
        rows = list(csv.reader(io.StringIO(text)))
        if rows and rows[0] and not rows[0][0].strip().lstrip("-").isdigit():
            header, rows = rows[0], rows[1:]
        else:
            header = None
        return header, rows

    @staticmethod
    def _parse_klines(rows):
        bars = []
        for row in rows:
            if len(row) < 8:
                continue
            ts = int(row[0])
            if ts > 1e14:  # some newer files use microseconds
                ts //= 1000
            bars.append(
                Bar(
                    day=datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date(),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    quote_volume=float(row[7]),
                )
            )
        return bars

    def daily_klines(self, symbol, day):
        url = f"{self.base}/data/futures/um/daily/klines/{symbol}/1d/{symbol}-1d-{day:%Y-%m-%d}.zip"
        got = self._zip_csv(url)
        return self._parse_klines(got[1]) if got else None

    def monthly_klines(self, symbol, year, month):
        url = f"{self.base}/data/futures/um/monthly/klines/{symbol}/1d/{symbol}-1d-{year:04d}-{month:02d}.zip"
        got = self._zip_csv(url)
        return self._parse_klines(got[1]) if got else None

    def klines(self, symbol, today, months):
        """Daily candles for the last `months` months up to the newest file available.

        Past months come from monthly files; the current month (and last month, until its
        monthly file is published) from daily files. Walks back until the coin's listing.
        """
        bars = {}
        y, m = today.year, today.month
        # Current month: daily files
        d = date(y, m, 1)
        while d < today:
            got = self.daily_klines(symbol, d)
            for b in got or []:
                bars[b.day] = b
            d += timedelta(days=1)
        for i in range(1, months + 1):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
            got = self.monthly_klines(symbol, y, m)
            if got is None and i == 1:
                # Last month's monthly file may not be published yet: use daily files
                d = date(y, m, 1)
                got = []
                while d.month == m:
                    got += self.daily_klines(symbol, d) or []
                    d += timedelta(days=1)
            if not got:
                break  # before the coin was listed
            for b in got:
                bars[b.day] = b
        return [bars[k] for k in sorted(bars)]

    def metrics_day(self, symbol, day):
        """Last open-interest reading of that UTC day, or None if the file is missing."""
        url = f"{self.base}/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day:%Y-%m-%d}.zip"
        got = self._zip_csv(url)
        if not got:
            return None
        header, rows = got
        idx = {name: i for i, name in enumerate(header or [])}
        i_c, i_v = idx.get("sum_open_interest", 2), idx.get("sum_open_interest_value", 3)
        for row in reversed(rows):
            try:
                c, v = float(row[i_c]), float(row[i_v])
            except (ValueError, IndexError):
                continue
            if c > 0:
                return OIPoint(day, c, v)
        return None

    def open_interest(self, symbol, latest_day, change_days, search_back=4):
        """(latest OI, OI `change_days` earlier). Either may be None."""
        now = None
        for k in range(search_back + 1):
            now = self.metrics_day(symbol, latest_day - timedelta(days=k))
            if now:
                break
        if not now:
            return None, None
        before = None
        for k in range(2):
            before = self.metrics_day(symbol, now.day - timedelta(days=change_days + k))
            if before:
                break
        return now, before


# ---------------------------------------------------------------- Binance live API (optional)


class BinanceLive:
    """Binance's live futures API. Blocked from US IPs, so every call may fail; callers handle None."""

    def __init__(self, http, base_url):
        self.http = http
        self.base = base_url.rstrip("/")

    def _get(self, path):
        try:
            return self.http.request("GET", self.base + path).json()
        except Exception as e:  # noqa: BLE001 - unreachable from the US is expected
            log.info("Binance live API %s unavailable: %s", path, e)
            return None

    def exchange_info(self):
        return self._get("/fapi/v1/exchangeInfo")

    def funding_8h(self):
        """{symbol: funding rate per 8h, in %} or None."""
        prem = self._get("/fapi/v1/premiumIndex")
        if not prem:
            return None
        intervals = {x["symbol"]: x.get("fundingIntervalHours", 8) for x in (self._get("/fapi/v1/fundingInfo") or [])}
        out = {}
        for x in prem:
            try:
                rate = float(x["lastFundingRate"]) * 100
            except (KeyError, TypeError, ValueError):
                continue
            out[x["symbol"]] = rate * 8 / (intervals.get(x["symbol"]) or 8)
        return out


# ---------------------------------------------------------------- Hyperliquid


@dataclass
class HLCoin:
    name: str
    volume_24h: float
    oi_usd: float
    funding_8h_pct: float
    delisted: bool


def hyperliquid_coins(http, url):
    r = http.request("POST", url, json={"type": "metaAndAssetCtxs"})
    meta, ctxs = r.json()
    out = {}
    for asset, ctx in zip(meta["universe"], ctxs):
        try:
            mark = float(ctx.get("markPx") or ctx.get("oraclePx") or 0)
            out[asset["name"]] = HLCoin(
                name=asset["name"],
                volume_24h=float(ctx.get("dayNtlVlm") or 0),
                oi_usd=float(ctx.get("openInterest") or 0) * mark,
                funding_8h_pct=float(ctx.get("funding") or 0) * 8 * 100,  # HL funding is hourly
                delisted=bool(asset.get("isDelisted")),
            )
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- Bybit (kline fallback)


def bybit_klines(http, base_url, symbol, limit=1000):
    r = http.request(
        "GET",
        base_url.rstrip("/") + "/v5/market/kline",
        params={"category": "linear", "symbol": symbol, "interval": "D", "limit": limit},
    )
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit {symbol}: {data.get('retMsg')}")
    bars = []
    for row in data["result"]["list"]:  # newest first: start, open, high, low, close, volume, turnover
        bars.append(
            Bar(
                day=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).date(),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                quote_volume=float(row[6]),
            )
        )
    return sorted(bars, key=lambda b: b.day)


def parallel_map(fn, items, workers):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(zip(items, ex.map(fn, items)))
