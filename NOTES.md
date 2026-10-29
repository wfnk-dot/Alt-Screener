# Build notes

## Data sources, checked from GitHub Actions (29 Oct 2026)

GitHub's runners are in the US, so every source was tested from there, not just from the build machine.

| Source | From GitHub Actions | Used for |
|---|---|---|
| Binance archive (data.binance.vision): symbol list, daily/monthly klines, daily metrics | ✅ works | Candles, volume, ATR, open interest |
| Hyperliquid `info` → `metaAndAssetCtxs` | ✅ works | Coin list, 24h volume, OI, funding |
| Binance live API (fapi.binance.com) | ❌ HTTP 451 "restricted location" | Used only if it ever answers (exchangeInfo, funding) |
| Bybit (`api.bybit.com` and `api.bytick.com`) | ❌ HTTP 403 "CloudFront … block access from your country" | Kline fallback (see below) |

A full dry run on GitHub Actions took about 50 seconds.

## ⚠️ Decision needed: Bybit fallback is blocked (failed twice, so I stopped)

The spec says: if the Binance archive is incomplete for a coin, fall back to Bybit for its candles.
Bybit blocks US IPs, including GitHub's runners, on both its hosts. The code still tries Bybit (so it would work
if that ever changes) and otherwise uses whatever the archive has. In practice the archive has been complete for
every coin that passed the filters, so this rarely matters. Options:

1. **Leave it as is** (current behaviour). Archive only; a coin with missing days is scored on the days it has.
2. **Use Hyperliquid daily candles as the fallback** (`candleSnapshot`, reachable from GitHub). Good for
   price/ATR/trend; volume would be Hyperliquid's, not Binance's, so the volume filter would use the archive's
   numbers only. Small change.
3. **Try OKX or Bitget public APIs** as the fallback. Not tested yet; both restrict US users, so they may also
   be blocked.

Recommendation: 1 for now, 2 if you ever see "Candles from Bybit" failing in the logs for a coin you care about.

## Other things the US block affects

- **Funding:** the archive only has *monthly* funding files (up to a month old), and the live Binance API is
  blocked, so funding comes from **Hyperliquid** (its hourly rate × 8 = per-8h). The report says which source
  each number came from.
- **Delisting/settling:** without Binance's live `exchangeInfo`, a contract counts as delisted when it has no
  candle in the last 3 days (`max_days_since_last_candle`). A contract that's announced for delisting but still
  trading can't be spotted until it stops. Index (BTCDOM etc.) and tradfi contracts are excluded by name in
  `config.yml`; Hyperliquid's main list doesn't carry stocks, which also keeps them out.
- **Archive lag:** daily candles lag 1-2 days, open interest about 1 day. The report shows the last candle date.

## TradingView format

TradingView's help pages couldn't be opened from the build machine. The file uses the same format as
TradingView's own watchlist export: one line, comma-separated `EXCHANGE:SYMBOL`, with `###Name` entries
starting sections. Its help page confirms the .txt, exchange-prefix and comma rules. Please check the
sections look right on the first import.

## Choices made where the spec left room

- **Range band:** "Range" is forced on the daily tag when all of the last 30 daily closes sit within a band
  2.5 × ATR wide centred on their average (i.e. within ±1.25 ATR). Applied to the daily tag only, since it's
  defined on daily closes.
- **Swing points are confirmed ones** (3 bars each side on daily, 2 on weekly). In a one-way crash no new swing
  low can be confirmed yet, so a coin can show `Range` rather than `Down` until it bounces. For example, STRK
  dropped from 0.127 to 0.046 in October but its last two confirmed lows were still rising.
- **"Rising over the last 5 bars"** = the EMA is higher now than 5 bars ago.
- **OI change** uses the number of contracts open, not their dollar value (which moves with price).
- **n/a metrics** (too little history) get a middle score (50th percentile), so they neither help nor hurt.
- **Brand-new coins:** under 15 daily candles skip the ATR filter (it can't be measured); the volume and OI
  filters still apply. Under 26 weekly candles = `New listing`.
- **Weekly candles** start on Monday (like Binance/TradingView); the current, unfinished week is included.
- **"Week of"** is the coming Monday (a Sunday run says "week of" the next day).
- **New/Dropped** compares with the most recent saved list from an earlier week. Manual runs save too, but a
  second run in the same week replaces that week's list rather than becoming "last week".

## Scheduling

GitHub's timer only understands UTC and often runs late. The workflow wakes every hour at :07; a quick check
compares UK time (GMT/BST) with `config.yml` and only runs the scan at the first wake-up at or after the post
time, up to 24 hours late, once per week. If a scheduled run fails, Discord gets one `⚠️ Screener failed`
message and it doesn't retry that week (no hourly spam); press **Run workflow** to retry.
