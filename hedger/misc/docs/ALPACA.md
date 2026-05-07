# Alpaca API Reference (for the hedger refactor)

This is a focused reference for refactoring `hedger` to use Alpaca as the primary (default) broker and data source. It pulls from the live Alpaca docs as of 2026-05-05.

---

## Where the docs live

Bookmark these. The first three are the load-bearing ones for the refactor.

- **alpaca-py SDK getting started** — [alpaca.markets/sdks/python/getting_started.html](https://alpaca.markets/sdks/python/getting_started.html)
- **alpaca-py Trading guide** (orders, positions, paper flag) — [alpaca.markets/sdks/python/trading.html](https://alpaca.markets/sdks/python/trading.html)
- **alpaca-py Market Data guide** (historical + streaming) — [alpaca.markets/sdks/python/market_data.html](https://alpaca.markets/sdks/python/market_data.html)
- **REST API reference** (every endpoint, every param) — [docs.alpaca.markets/reference](https://docs.alpaca.markets/reference)
- **Trading API getting started** (curl examples, request IDs) — [docs.alpaca.markets/docs/getting-started-with-trading-api](https://docs.alpaca.markets/docs/getting-started-with-trading-api)
- **Paper trading walkthrough** — [alpaca.markets/learn/start-paper-trading](https://alpaca.markets/learn/start-paper-trading)
- **Market Data API overview** (subscription tiers, IEX vs SIP) — [docs.alpaca.markets/docs/about-market-data-api](https://docs.alpaca.markets/docs/about-market-data-api)
- **WebSocket streaming docs** — [docs.alpaca.markets/docs/streaming-market-data](https://docs.alpaca.markets/docs/streaming-market-data)
- **Market Data FAQ** (IEX vs SIP feeds, halts, ticker changes) — [docs.alpaca.markets/docs/market-data-faq](https://docs.alpaca.markets/docs/market-data-faq)
- **alpaca-py GitHub** (source + examples) — [github.com/alpacahq/alpaca-py](https://github.com/alpacahq/alpaca-py) (examples folder is gold)
- **Account dashboard** (where you generate keys) — [app.alpaca.markets/account/login](https://app.alpaca.markets/account/login)

---

## SDK choice — only one matters

Use **`alpaca-py`** (the new official SDK). The older `alpaca-trade-api-python` is deprecated; ignore tutorials that import from it. `pip install alpaca-py`. Already in our `pyproject.toml`.

---

## Endpoints (memorise these three)

| Purpose | Paper | Live |
|---|---|---|
| Trading REST | `https://paper-api.alpaca.markets` | `https://api.alpaca.markets` |
| Market data REST | `https://data.alpaca.markets` (same URL for paper & live) | same |
| Account/order updates WS | `wss://paper-api.alpaca.markets/stream` | `wss://api.alpaca.markets/stream` |
| Market data WS | `wss://stream.data.alpaca.markets/v2/{feed}` where `{feed}` ∈ `iex` (free), `sip` (Algo Trader Plus), `test` | same |

For the SDK you don't construct these by hand — `paper=True` switches the trading client; the data client uses the same data URL regardless of paper/live.

---

## Auth

REST: two headers.

```
APCA-API-KEY-ID: <key>
APCA-API-SECRET-KEY: <secret>
```

SDK: just pass them positionally.

```python
from alpaca.trading.client import TradingClient
trading = TradingClient(api_key, secret_key, paper=True)
```

**Crypto historical data does not require keys** — `CryptoHistoricalDataClient()` works unauthenticated, you just get a lower rate limit. Stock data does require keys.

---

## The four SDK clients we need

```python
# Trading
from alpaca.trading.client import TradingClient
from alpaca.trading.stream import TradingStream  # account/order updates WS

# Market data
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.live import StockDataStream, CryptoDataStream  # market data WS

# News (no keys required)
from alpaca.data.historical.news import NewsClient
```

There are also `OptionHistoricalDataClient` and `OptionDataStream` if/when we add options.

---

## Minimal working examples

### Paper TradingClient

```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

trading = TradingClient(api_key, secret_key, paper=True)

# account info
account = trading.get_account()
print(account.buying_power, account.equity, account.pattern_day_trader)

# place a fractional market buy
order = trading.submit_order(
    MarketOrderRequest(
        symbol="SPY",
        qty=0.5,                   # fractional via non-integer qty
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
)

# or notional dollars instead of qty
order = trading.submit_order(
    MarketOrderRequest(
        symbol="SPY",
        notional=200,              # buy $200 worth
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
)

# positions
positions = trading.get_all_positions()
```

### Stock historical bars

```python
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime

data = StockHistoricalDataClient(api_key, secret_key)
bars = data.get_stock_bars(StockBarsRequest(
    symbol_or_symbols=["SPY", "QQQ"],
    timeframe=TimeFrame.Hour,
    start=datetime(2024, 1, 1),
    end=datetime(2024, 6, 1),
)).df  # multi-index pandas DataFrame
```

### Crypto historical bars (no keys)

```python
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

bars = CryptoHistoricalDataClient().get_crypto_bars(CryptoBarsRequest(
    symbol_or_symbols=["BTC/USD", "ETH/USD"],
    timeframe=TimeFrame.Day,
    start=datetime(2024, 1, 1),
)).df
```

Crypto symbols use `BASE/QUOTE` (slash, not hyphen).

### Order updates stream (live fills, cancels, rejections)

```python
from alpaca.trading.stream import TradingStream

stream = TradingStream(api_key, secret_key, paper=True)

async def handle(data):
    # data.event in {'new', 'fill', 'partial_fill', 'canceled', 'rejected', ...}
    print(data)

stream.subscribe_trade_updates(handle)
stream.run()
```

### Market data stream

```python
from alpaca.data.live import StockDataStream

stream = StockDataStream(api_key, secret_key)

async def on_bar(bar):
    print(bar.symbol, bar.close)

stream.subscribe_bars(on_bar, "SPY", "QQQ")
stream.run()
```

**Connection limit:** most subscriptions allow only **1 active data WebSocket connection per account**. If you open a second, you get HTTP 406 / "connection limit exceeded". The Trading stream and the Data stream count separately.

---

## Subscription tiers (matters for live, not paper)

- **Basic** (free, default for both Paper and Live): historical data is full coverage; **real-time stock data is IEX-only** (~3% of US volume). Fine for backtesting and most paper trading. Insufficient for serious low-latency live trading.
- **Algo Trader Plus** (paid): real-time SIP feed (CTA + UTP, full 100% market coverage), higher rate limits, options OPRA feed. ~$99/mo last I checked — verify current price.

For the hedger use case (1h–4h cadence, paper-first), **Basic is enough**. The IEX vs SIP distinction matters for sub-minute strategies; we don't have those.

---

## Rate limits

- Trading API: 200 requests/min (free tier), 1,000/min (funded), as cited in the earlier research.
- Market Data REST: up to 10,000/min on paid Algo Trader Plus plans; lower on Basic but we won't hit it at our cadence.
- WebSocket: unlimited messages once connected, but 1 active connection per account per stream type.
- Every response includes `X-Request-ID` — log it. Alpaca support will ask for it in any ticket.

---

## Paper-trading specifics

- Up to **3 paper trading accounts** per Alpaca user.
- Paper account has a separate set of keys from live. Don't mix them up.
- Switching between paper and live in the SDK is one flag: `TradingClient(..., paper=True)` vs `False`. **Same code path**, which is why our `AlpacaBroker` can already do this.
- Paper trading does not model: slippage, partial fills under tight liquidity, market-impact, halts. Hence why our `PaperBroker` (with `fee_bps`/`slippage_bps`) is still useful even with Alpaca paper available — it lets us simulate worse conditions than Alpaca's idealised paper engine.
- The recommended onboarding flow per [Alpaca's start-paper-trading guide](https://alpaca.markets/learn/start-paper-trading): log in → upper-left dropdown → choose "Paper Trading" → API Keys panel → generate → **save the secret immediately, it's shown once**.

---

## What changes in hedger for the Alpaca-first refactor

Concrete file-by-file picture for the Claude Code agent to follow.

### 1. `hedger/data/sources.py` — rewrite `AlpacaSource`

Current shipped code uses the SDK already, but verify against the real API. Specifically:

- Use `StockHistoricalDataClient.get_stock_bars()` for stocks, `CryptoHistoricalDataClient.get_crypto_bars()` for crypto. Pick by symbol shape: contains `/` → crypto, otherwise stock.
- Map `TimeFrame` strings to `alpaca.data.timeframe.TimeFrame` enum: `"1m"` → `TimeFrame.Minute`, `"1h"` → `TimeFrame.Hour`, `"1d"` → `TimeFrame.Day`. There's also `TimeFrame(amount, TimeFrameUnit.Minute)` for non-standard intervals like 4h.
- The SDK returns a multi-index `DataFrame` (symbol, timestamp). Iterate it to yield `Bar` dataclasses matching `hedger/base.py`.
- Make `AlpacaSource` the default in `make_source()` (currently yfinance is). Demote yfinance to a fallback.

### 2. `hedger/execution/brokers.py` — verify `AlpacaBroker`

- Constructor: `AlpacaBroker(api_key, secret_key, paper=True)` — already correct shape.
- `submit(order)`: build `MarketOrderRequest` (or `LimitOrderRequest`); pass to `TradingClient.submit_order()`; wait for fill via either polling `get_order_by_id()` or — better — subscribe to `TradingStream` once and route fills back to the runner.
- `positions()`: `trading.get_all_positions()` returns `Position` objects with `symbol`, `qty`, `avg_entry_price`, `market_value`, `unrealized_pl`. Map to our `Position` dataclass.
- `equity()`: `trading.get_account().equity`.
- Add fractional-share support: pass `qty=0.5` directly, or pass `notional=200`. Our sizer should produce notional dollars and let the broker convert.

### 3. `hedger/config.py` — Alpaca-first defaults

```toml
[broker]
kind = "alpaca"
paper = true
# keys come from env: ALPACA_API_KEY, ALPACA_SECRET_KEY
# fallback: ALPACA_API_KEY_ID, ALPACA_SECRET_KEY (matching docs)

[data]
source = "alpaca"
# stock_feed = "iex"   # default; switch to "sip" if Algo Trader Plus
```

### 4. `hedger/util.py::check_requirements()` — extend

Doctor should verify:
- `alpaca-py` installed (already in pyproject)
- `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` env vars present
- A successful `TradingClient(...).get_account()` round-trip (one call, paper account, fail-soft with a clear message)
- Optional: warn if `paper=False` but the account number doesn't have the live prefix

### 5. New: order-update streaming wired into the runner

The runner currently submits orders synchronously and assumes immediate fills. Real Alpaca orders are async. Fix:

- On runner startup, spawn a background thread/task running `TradingStream.run()`.
- Stream callback writes fills into `mall["fills"]` as they arrive.
- `runner.tick()` doesn't wait for fills; fills are consumed at the next tick by reading `mall["fills"]` deltas since last tick.

This is the single most important behavioural change — without it, the system can submit duplicate orders or believe it's flat when it isn't.

### 6. `hedger/data/sources.py::AlpacaNews` (new)

Add a thin wrapper over `NewsClient` that yields normalised news items into `mall["news"]`. The `llm_news` strategy currently has its own news-fetch — this lets it consume from the mall instead, which is the right pattern (see the `data-pipeline` skill).

```python
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

news = NewsClient().get_news(NewsRequest(symbols="SPY,QQQ", start=...))
# .data is a list of News objects with .headline, .summary, .symbols, .created_at, .url
```

No keys required for the news endpoint.

### 7. Remove or demote: yfinance, ccxt

Don't delete — keep as fallbacks for offline backtesting (yfinance) and for crypto-on-non-Alpaca venues (ccxt). But Alpaca becomes the default on a fresh `hedger doctor` and the example configs.

---

## Gotchas worth pre-empting

1. **Ticker symbol changes**: when Facebook → Meta the ticker changed FB → META; latest endpoints return the data as it was at the time of the trade, not under the current ticker. If you query `/latest` and a symbol seems missing, check whether it was renamed.
2. **Halted symbols** return no recent data — verify against the Nasdaq halt list before assuming a bug.
3. **Bar pagination**: bars are sorted by symbol first, then timestamp. A multi-symbol request hitting the limit will return only the first symbol's bars. Use `next_page_token` to continue. The SDK handles this for you; the raw REST call doesn't.
4. **`time_in_force` for crypto**: only `gtc`, `ioc`, `fok` work. Using `day` will raise.
5. **Crypto trades 24/7**, US equities don't. Schedule equity ticks against `trading.get_clock()` rather than wall time, and keep crypto on a separate cron.
6. **Don't hardcode the data URL** — let the SDK pick. It changes occasionally for paid feed routing.

---

## Examples worth reading

The official `alpaca-py` examples folder is the best learning material — [github.com/alpacahq/alpaca-py/tree/master/examples](https://github.com/alpacahq/alpaca-py/tree/master/examples) — particularly `paper-trading-basics`, `historical-data`, `live-data-streaming`, and `options-trading-basic`. Worth a `git clone` next to the hedger repo.
