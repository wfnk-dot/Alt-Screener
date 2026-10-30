# Alt Screener

Every Sunday (18:00 UK by default) this scans Binance USDT perpetuals, keeps the ones that are also
active on Hyperliquid, and picks the **10 most active, liquid alts** for the leverage watchlist.
Each gets a daily and weekly trend tag. Output:

- **Discord post**: @here ping, top-10 table as a picture, new/dropped coins, `watchlist.txt` attached
- **`latest/watchlist.txt`**: TradingView import file (dated copies in `history/`)
- **`latest/report.md`**: every coin that passed the filters

It also sends **Dorito alerts** (Leledc exhaustion, confirmed on candle close) for the top 50 coins on the
4h, daily, 3-day and weekly to a separate Discord channel.

Mid-week (Wednesday by default) it pings the watchlist channel if a coin has jumped into the top 10.
A **catalysts** channel gets the week ahead every Sunday (high-impact US news, big-tech earnings, how BTC
reacted to past FOMC/CPI) and reminders 1 day, 1 hour and 15 minutes before each event.

Runs free on GitHub Actions. No AI, no paid services, no keys except the Discord webhook (a GitHub secret).
Not trade advice: it picks what to watch, not what to trade.

**Setup:** see [SETUP.md](SETUP.md). **Settings:** [config.yml](config.yml). **Build notes:** [NOTES.md](NOTES.md).

## How it works

1. **Universe:** Binance USDT perps (from the data.binance.vision archive) that are also listed on Hyperliquid
   (`1000PEPE` ↔ `kPEPE` matched automatically, extra aliases in config). Core coins, gold, stablecoins,
   index and tradfi contracts are excluded. Contracts with no recent candles (delisted/settling) are dropped.
2. **Filters** (all must pass): Binance 7-day avg daily volume ≥ $30M, Binance OI ≥ $15M,
   Hyperliquid 24h volume ≥ $3M, Hyperliquid OI ≥ $3M, daily ATR(14) ≥ 4% of price.
3. **Score:** percentile rank among the survivors of ATR% (30%), 7d/30d volume (25%), |OI change 7d| (20%),
   |7d return| (15%), Binance OI size (10%). Top 10 make the list.
4. **Tags (info only):** `D: Up · W: Range` trend, weekly RSI(14) and how many of the last 12 weekly
   RSIs were above 50, OI change, funding (flagged if |rate| ≥ 0.05%/8h), New listing.

## Run it yourself

```bash
pip install -r requirements.txt
python -m screener --dry-run   # prints the post instead of sending it
python -m pytest -q
```
