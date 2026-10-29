# Setup (about 5 minutes, no coding)

Everything runs on GitHub for free. You only need to do steps 1 and 2 once.

---

## 1. Connect Discord

**Make the webhook in Discord**

1. In Discord, hover over the channel you want the post in and click the **⚙️ gear** (Edit Channel).
2. Click **Integrations** → **Webhooks** → **New Webhook**.
3. Optional: rename it (e.g. "Alt Screener").
4. Click **Copy Webhook URL**. Keep it private: anyone with it can post in that channel.

**Save it in GitHub as a secret**

1. Open the repo on GitHub: `github.com/wfnk-dot/alt-screener`.
2. Click **Settings** (top bar) → **Secrets and variables** (left menu) → **Actions**.
3. Click **New repository secret**.
4. **Name:** `DISCORD_WEBHOOK_URL` (exactly this).
5. **Secret:** paste the webhook URL.
6. Click **Add secret**.

---

## 2. Run it once by hand

1. In the repo, click **Actions** (top bar).
   - If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. On the left, click **Alt Screener**.
3. On the right, click **Run workflow** → leave "Dry run" unticked → green **Run workflow** button.
4. Wait about 1-2 minutes (refresh the page). A green ✅ means it worked.
5. Check Discord: you should see **"Alt watchlist: Mon … – Sun …"** with the table picture and `watchlist.txt` attached.

If it shows a red ❌, Discord gets a `⚠️ Screener failed: …` message with the reason.
Click the failed run on GitHub to see the full log.

From now on it posts by itself every **Sunday at 18:00 UK time** (usually 5-30 minutes after, as GitHub's timer runs a bit late). You can press **Run workflow** any time for a fresh list.

> Tick **Dry run** if you just want to try something: it builds the list and shows the post in the run's log, without posting to Discord or saving anything.

---

## 3. Change the settings

All settings are in **`config.yml`**, with a note next to each one.

1. In the repo, click **`config.yml`**.
2. Click the **✏️ pencil** (top right of the file).
3. Change the number(s). Keep the spaces at the start of each line as they are.
4. Click **Commit changes…** → **Commit changes**.

The next run uses the new settings. Common changes:

| What | Where in `config.yml` |
|---|---|
| How many alts on the list | `top_n: 10` |
| Post day / time (UK time) | `schedule:` → `day: Sunday`, `time: "18:00"` |
| Volume / OI / ATR minimums | `filters:` |
| Ranking weights | `ranking_weights:` (keep the total at 100) |
| Core symbols (BTC, ETH, SOL, HYPE, gold) | `tradingview:` → `core:` |
| Gold symbol | the `OANDA:XAUUSD` line under `core:` |
| Coins to never include | `universe:` → `exclude:` |
| Binance ↔ Hyperliquid name matches | `universe:` → `hyperliquid_aliases:` |
| Extreme funding level | `indicators:` → `funding_extreme_pct` |
| Ping (@here / @everyone / none) | `discord:` → `ping:` |
| Columns in the table picture | `table:` → `columns:` (delete a line to hide one, move lines to reorder) |

The **Tests** run (Actions tab) checks the code still works after each change. If you break `config.yml` (usually a missing space or colon), it goes red; click **History** on the file to see and undo your change.

---

## 4. Import the watchlist into TradingView

1. Download `watchlist.txt` from the Discord post (or from `latest/watchlist.txt` in the repo → **Download raw file** button).
2. In TradingView, open the **Watchlist** panel (right side).
3. Click the watchlist name at the top to open the **Watchlist menu** → **Import list…**
4. Pick `watchlist.txt`.

You get a new list with a **CORE** section (BTC, ETH, SOL, HYPE, gold) and an **ALTS** section with this week's coins.

---

## 5. Dorito alerts (separate channel)

The alerts check the top 50 coins every hour for Leledc exhaustion Doritos on the **4h, daily, 3-day and
weekly**. An alert only goes out once the candle has **closed** with the Dorito, so ones that appear
mid-candle and then disappear never alert. They post to their own channel.

1. In Discord, make a webhook in the alerts channel, the same way as step 1 (⚙️ → Integrations →
   Webhooks → New Webhook → Copy Webhook URL).
2. On GitHub: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
3. **Name:** `DISCORD_ALERTS_WEBHOOK_URL` · **Secret:** paste the new link → **Add secret**.
4. Test it: **Actions** → **Dorito Alerts** → **Run workflow**.

Alerts arrive within about an hour of the candle closing (GitHub's timer runs every hour, sometimes late).
Settings are under `alerts:` in `config.yml` (timeframes, how many coins, the indicator's two numbers, ping).
If the alerts ever stop working, GitHub emails you about the failed run.

---

## Where things are

- `latest/report.md`: every coin that passed the filters, with all the numbers (click it on GitHub to read it as a table).
- `latest/watchlist.txt`: this week's TradingView file. Past weeks are in `history/`.
- `state.json`: last weeks' lists, used for "New this week" / "Dropped off". Don't edit it.
