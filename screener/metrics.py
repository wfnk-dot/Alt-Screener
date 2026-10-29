"""Per-coin numbers used by the filters, ranking and report."""
from dataclasses import dataclass, field

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
