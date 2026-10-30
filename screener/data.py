"""Public data sources: Binance archive (data.binance.vision), Hyperliquid, Bybit, Binance live API.

Binance's live futures API blocks US IPs (GitHub's runners are in the US), so the
archive is the main source and the live API is only used when it answers.
"""
import csv
import io
import logging
import os
import re
import threading
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

    def open_interest(self, symbol, latest_day, lookbacks, search_back=4):
        """(latest OI, {days: OI that many days earlier}). Missing readings are None."""
        now = None
        for k in range(search_back + 1):
            now = self.metrics_day(symbol, latest_day - timedelta(days=k))
            if now:
                break
        if not now:
            return None, {}
        before = {}
        for days in lookbacks:
            point = None
            for k in range(2):
                point = self.metrics_day(symbol, now.day - timedelta(days=days + k))
                if point:
                    break
            before[days] = point
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


_HL_INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "12h": 43_200_000, "1d": 86_400_000}


class Throttle:
    """Spaces out calls so Hyperliquid's per-minute request limit isn't hit."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            delay = self.last + self.seconds - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


def hyperliquid_candles(http, url, coin, start_ms, end_ms, interval="1d", throttle=None):
    """Hyperliquid candles, oldest first.

    Daily ("1d"): bars keyed by UTC date, and the last one is today's, still forming, so its
    close is the live price. Intraday ("4h", "1h"): only closed candles, keyed by start time.
    """
    if throttle:
        throttle.wait()
    r = http.request(
        "POST",
        url,
        json={"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}},
    )
    step = _HL_INTERVAL_MS[interval]
    bars = []
    for c in r.json():
        t = int(c["t"])
        if interval != "1d" and t + step > end_ms:
            continue  # still forming
        when = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        bars.append(
            Bar(
                day=when.date() if interval == "1d" else when,
                open=float(c["o"]),
                high=float(c["h"]),
                low=float(c["l"]),
                close=float(c["c"]),
                quote_volume=float(c["v"]) * float(c["c"]),
            )
        )
    return sorted(bars, key=lambda b: b.day)


def binance_web_klines(http, base_url, symbol, interval, pages=2, throttle=None):
    """Binance USDT-perp candles, live, via www.binance.com (reachable from GitHub's US servers,
    unlike fapi.binance.com). Up to 1500 candles per page, oldest first; the last one may still be
    forming. Daily-or-longer candles are keyed by UTC date, intraday by start time."""
    bars, end = [], None
    for _ in range(pages):
        if throttle:
            throttle.wait()
        params = {"symbol": symbol, "interval": interval, "limit": 1500}
        if end is not None:
            params["endTime"] = end
        rows = http.request("GET", base_url.rstrip("/") + "/fapi/v1/klines", params=params).json()
        if not rows:
            break
        page = []
        for r in rows:
            when = datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc)
            page.append(Bar(when.date() if interval.endswith(("d", "w")) else when,
                            float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])))
        bars = page + bars
        if len(rows) < 1500:
            break
        end = int(rows[0][0]) - 1
    return bars


def binance_oi_history(http, base_url, symbol, period="1h", limit=200, throttle=None):
    """Binance open interest snapshots, oldest first: [(UTC time, contracts, USD value)]."""
    if throttle:
        throttle.wait()
    rows = http.request(
        "GET", base_url.rstrip("/") + "/futures/data/openInterestHist",
        params={"symbol": symbol, "period": period, "limit": limit},
    ).json()
    out = []
    for r in rows:
        when = datetime.fromtimestamp(int(r["timestamp"]) / 1000, tz=timezone.utc)
        out.append((when, float(r["sumOpenInterest"]), float(r["sumOpenInterestValue"])))
    return sorted(out)


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
