"""Per-coin numbers used by the filters, ranking and report."""
from dataclasses import dataclass, field
from datetime import timedelta

from .indicators import atr_pct


@dataclass
class Coin:
    symbol: str                 # Binance symbol, e.g. 1000PEPEUSDT
    base: str                   # e.g. 1000PEPE
    hl_name: str                # Hyperliquid name, e.g. kPEPE
    bars: list                  # daily candles, oldest first
    source: str = "Binance archive"
    hl_volume_24h: float = 0.0
    hl_oi_usd: float = 0.0
    oi_usd: float | None = None          # Binance OI, USD
    oi_change_pct: float | None = None   # Binance OI (contracts) change over the week, %
    oi_change_3d_pct: float | None = None  # same over 3 days
    return_weekend_pct: float | None = None  # Friday close -> Sunday close (or now, mid-weekend)
    price_source: str = "Binance archive"
    beta_btc: float | None = None        # coin's daily move per 1% BTC move
    corr_btc: float | None = None        # 0 = ignores BTC, 1 = moves exactly with BTC
    funding_8h_pct: float | None = None
    funding_source: str = ""
    # filled by compute_metrics
    avg_vol_7d: float | None = None
    vol_ratio: float | None = None
    atr_pct: float | None = None
    return_7d_pct: float | None = None
    # filled later
    score: float | None = None
    tags: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


def compute_metrics(coin, cfg):
    ind = cfg["indicators"]
    bars = coin.bars
    short, long_, ch = ind["volume_short_days"], ind["volume_long_days"], ind["change_days"]
    vols = [b.quote_volume for b in bars]
    # A brand-new coin averages over the days it has
    coin.avg_vol_7d = _avg(vols[-short:])
    if len(bars) >= long_:
        coin.vol_ratio = _avg(vols[-short:]) / _avg(vols[-long_:]) if _avg(vols[-long_:]) else None
    coin.atr_pct = atr_pct(bars, ind["atr_period"])
    if len(bars) > ch and bars[-ch - 1].close:
        coin.return_7d_pct = (bars[-1].close / bars[-ch - 1].close - 1) * 100
    return coin


def live_price_changes(coin, hl_bars, today, source="Hyperliquid (live)"):
    """Price moves from live daily candles (Binance's or Hyperliquid's): the last one is today's, still
    forming, so they run up to now and include the weekend. Sets the 7-day and weekend moves."""
    by_day = {b.day: b for b in hl_bars}
    if not hl_bars or hl_bars[-1].day != today:
        return coin
    now = hl_bars[-1].close
    week_ago = by_day.get(today - timedelta(days=7))
    if week_ago and week_ago.close:
        coin.return_7d_pct = (now / week_ago.close - 1) * 100
        coin.price_source = source
    saturday = today - timedelta(days=(today.weekday() - 5) % 7)
    sat, sun = by_day.get(saturday), by_day.get(saturday + timedelta(days=1))
    if sat and sat.open:
        end = sun.close if sun else sat.close
        coin.return_weekend_pct = (end / sat.open - 1) * 100
    return coin


def beta_corr(bars, btc_bars, days, min_days=10):
    """Beta and correlation of daily returns vs BTC over the last `days` days both traded.
    (None, None) when there's too little overlap."""
    btc = {b.day: b.close for b in btc_bars}
    pairs = []
    for prev, cur in zip(bars, bars[1:]):
        if (cur.day - prev.day).days == 1 and prev.day in btc and cur.day in btc and prev.close and btc[prev.day]:
            pairs.append((cur.close / prev.close - 1, btc[cur.day] / btc[prev.day] - 1))
    pairs = pairs[-days:]
    if len(pairs) < min_days:
        return None, None
    n = len(pairs)
    mc = sum(p[0] for p in pairs) / n
    mb = sum(p[1] for p in pairs) / n
    cov = sum((c - mc) * (b - mb) for c, b in pairs) / n
    var_c = sum((c - mc) ** 2 for c, _ in pairs) / n
    var_b = sum((b - mb) ** 2 for _, b in pairs) / n
    if not var_b or not var_c:
        return None, None
    return cov / var_b, cov / (var_b * var_c) ** 0.5
