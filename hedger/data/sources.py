"""Data sources.

Adapters that yield `Bar`s, conforming to the `DataSource` protocol. Each is
optional: import errors are caught and turned into informative messages so a
user who only wants crypto doesn't have to install Alpaca, and vice versa.

Convention: keep the adapter thin. Anything cleaning or feature-engineering
data goes in `hedger.features`.

`AlpacaSource` is the recommended default: paper- and live-grade, supports
both equities and crypto via one client family. `YFinanceSource` and
`CCXTSource` remain as offline / non-Alpaca fallbacks.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable, Iterator

from hedger.base import AssetClass, Bar, Symbol


# ---------------------------------------------------------------------------
# yfinance — free, daily/intraday up to 60 days, no API key
# ---------------------------------------------------------------------------

class YFinanceSource:
    """Free OHLCV via yfinance. Best for daily research and offline backtests.

    Limitations: intraday history is shallow, fills are noisy, rate-limited.
    Don't ship live with this; use it to bootstrap research.
    """

    name = "yfinance"

    def bars(
        self,
        symbol: Symbol,
        *,
        start: datetime,
        end: datetime,
        timeframe: str = "1d",
    ) -> Iterable[Bar]:
        try:
            import yfinance as yf
        except ImportError as e:
            raise ImportError("`pip install yfinance` to use YFinanceSource.") from e
        interval = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m",
                    "1d": "1d", "1wk": "1wk"}.get(timeframe, timeframe)
        df = yf.download(
            symbol.ticker, start=start, end=end, interval=interval,
            auto_adjust=True, progress=False, threads=False,
        )
        if df.empty:
            return
        # yfinance occasionally returns a MultiIndex; flatten if so.
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        for ts, row in df.iterrows():
            yield Bar(
                symbol=symbol,
                ts=ts.to_pydatetime(),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
            )


# ---------------------------------------------------------------------------
# Alpaca — US equities/ETF/options/crypto, paper or live
# ---------------------------------------------------------------------------

class AlpacaSource:
    """Alpaca historical bars via the official `alpaca-py` SDK.

    Falls back to ``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY`` env vars when
    no keys are passed. Crypto historical data does not require keys.

    >>> AlpacaSource.name
    'alpaca'
    """

    name = "alpaca"

    def __init__(self, api_key: str | None = None, secret: str | None = None):
        try:
            from alpaca.data.historical import (
                CryptoHistoricalDataClient,
                StockHistoricalDataClient,
            )
        except ImportError as e:
            raise ImportError("`pip install alpaca-py` to use AlpacaSource.") from e
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY")
        self.secret = secret or os.environ.get("ALPACA_SECRET_KEY")
        self._stock = (
            StockHistoricalDataClient(self.api_key, self.secret)
            if self.api_key and self.secret
            else None
        )
        self._crypto = CryptoHistoricalDataClient()  # no key needed for crypto data

    @staticmethod
    def _timeframe(timeframe: str):
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        unit_map = {
            "m": TimeFrameUnit.Minute,
            "h": TimeFrameUnit.Hour,
            "d": TimeFrameUnit.Day,
        }
        n, u = int(timeframe[:-1]), timeframe[-1]
        return TimeFrame(n, unit_map[u])

    def bars(
        self,
        symbol: Symbol,
        *,
        start: datetime,
        end: datetime,
        timeframe: str = "1h",
    ) -> Iterator[Bar]:
        from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest

        tf = self._timeframe(timeframe)
        is_crypto = (
            symbol.asset_class is AssetClass.CRYPTO or "/" in symbol.ticker
        )
        if is_crypto:
            req = CryptoBarsRequest(
                symbol_or_symbols=[symbol.ticker], timeframe=tf,
                start=start, end=end,
            )
            res = self._crypto.get_crypto_bars(req)
        else:
            if not self._stock:
                raise RuntimeError(
                    "AlpacaSource: ALPACA_API_KEY/ALPACA_SECRET_KEY missing for stock bars."
                )
            req = StockBarsRequest(
                symbol_or_symbols=[symbol.ticker], timeframe=tf,
                start=start, end=end,
            )
            res = self._stock.get_stock_bars(req)
        ticker_bars = res.data.get(symbol.ticker, []) if hasattr(res, "data") else res[symbol.ticker]
        for bar in ticker_bars:
            yield Bar(
                symbol=symbol,
                ts=bar.timestamp,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            )


# ---------------------------------------------------------------------------
# AlpacaNews — feeds mall["news"] with normalised headlines
# ---------------------------------------------------------------------------

class AlpacaNews:
    """Wrapper over alpaca-py's ``NewsClient``.

    Yields plain dicts (one per headline) so callers can persist directly to a
    JsonlStore — the ``llm_news`` strategy reads its headlines from
    ``context['news']``, which the runner builds from this stream.

    >>> AlpacaNews.name
    'alpaca_news'
    """

    name = "alpaca_news"

    def __init__(self, api_key: str | None = None, secret: str | None = None):
        try:
            from alpaca.data.historical.news import NewsClient
        except ImportError as e:
            raise ImportError("`pip install alpaca-py` to use AlpacaNews.") from e
        key = api_key or os.environ.get("ALPACA_API_KEY")
        sec = secret or os.environ.get("ALPACA_SECRET_KEY")
        if not (key and sec):
            raise RuntimeError(
                "AlpacaNews requires ALPACA_API_KEY and ALPACA_SECRET_KEY in env."
            )
        self._client = NewsClient(key, sec)

    def fetch(
        self,
        symbols: Iterable[str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
    ) -> Iterator[dict]:
        from alpaca.data.requests import NewsRequest
        sym_str = ",".join(symbols) if not isinstance(symbols, str) else symbols
        kwargs: dict = {"symbols": sym_str, "limit": limit}
        if start is not None:
            kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
        res = self._client.get_news(NewsRequest(**kwargs))
        items = res.data.get("news", []) if hasattr(res, "data") else list(res)
        for n in items:
            yield {
                "id": getattr(n, "id", None),
                "headline": getattr(n, "headline", ""),
                "summary": getattr(n, "summary", "") or "",
                "symbols": list(getattr(n, "symbols", []) or []),
                "created_at": (n.created_at.isoformat()
                               if getattr(n, "created_at", None) else None),
                "url": getattr(n, "url", None),
                "author": getattr(n, "author", None),
            }


# ---------------------------------------------------------------------------
# CCXT — unified API across most crypto exchanges
# ---------------------------------------------------------------------------

class CCXTSource:
    """OHLCV via CCXT. Pass an exchange id like 'kraken' or 'binance'.

    Useful when Alpaca doesn't list a venue you want to trade on.
    """

    name = "ccxt"

    def __init__(self, exchange: str, *, api_key: str | None = None, secret: str | None = None):
        try:
            import ccxt
        except ImportError as e:
            raise ImportError("`pip install ccxt` to use CCXTSource.") from e
        cls = getattr(ccxt, exchange)
        self.exchange = cls({"apiKey": api_key, "secret": secret, "enableRateLimit": True})

    def bars(
        self,
        symbol: Symbol,
        *,
        start: datetime,
        end: datetime,
        timeframe: str = "1h",
    ) -> Iterable[Bar]:
        # ccxt expects ms epoch
        since = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        while since < end_ms:
            chunk = self.exchange.fetch_ohlcv(symbol.ticker, timeframe=timeframe,
                                              since=since, limit=1000)
            if not chunk:
                break
            for ts_ms, o, h, l, c, v in chunk:
                if ts_ms > end_ms:
                    return
                yield Bar(
                    symbol=symbol,
                    ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                    open=float(o), high=float(h), low=float(l),
                    close=float(c), volume=float(v),
                )
            since = chunk[-1][0] + 1


def make_source(spec: str = "alpaca", **kwargs):
    """Factory: 'alpaca' | 'yfinance' | 'ccxt:kraken' -> DataSource.

    Default is ``alpaca`` (Alpaca-first); ``yfinance`` remains the offline
    fallback for research.

    >>> isinstance(make_source('yfinance'), YFinanceSource)
    True
    """
    if spec == "yfinance":
        return YFinanceSource()
    if spec == "alpaca":
        return AlpacaSource(**kwargs)
    if spec == "alpaca_news":
        return AlpacaNews(**kwargs)
    if spec.startswith("ccxt:"):
        return CCXTSource(spec.split(":", 1)[1], **kwargs)
    raise ValueError(
        f"Unknown source spec: {spec!r}. "
        "Use 'alpaca', 'yfinance', 'alpaca_news', or 'ccxt:<exchange>'."
    )


__all__ = ["YFinanceSource", "AlpacaSource", "AlpacaNews", "CCXTSource", "make_source"]
