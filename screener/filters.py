"""Pass/fail filters. A coin must pass all of them."""


def apply_filters(coin, cfg):
    """Returns True if the coin passes. Reasons for failing are stored on coin.failed."""
    f = cfg["filters"]
    failed = []
    if coin.avg_vol_7d is None or coin.avg_vol_7d < f["binance_min_avg_daily_volume_7d"]:
        failed.append("Binance 7d volume")
    if coin.oi_usd is None or coin.oi_usd < f["binance_min_open_interest"]:
        failed.append("Binance OI")
    if coin.hl_volume_24h < f["hyperliquid_min_volume_24h"]:
        failed.append("Hyperliquid volume")
    if coin.hl_oi_usd < f["hyperliquid_min_open_interest"]:
        failed.append("Hyperliquid OI")
    # Too new to measure ATR: allowed through (spec: no minimum listing age)
    if len(coin.bars) >= f["min_days_for_atr_filter"]:
        if coin.atr_pct is None or coin.atr_pct < f["min_atr_pct"]:
            failed.append("ATR%")
    coin.failed = failed
    return not failed
