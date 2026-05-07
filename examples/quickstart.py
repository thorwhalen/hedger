"""Quickstart: backtest sma_crossover on SPY+QQQ over the last year.

Run with:

    pip install -e .
    python examples/quickstart.py

Then graduate to:

    hedger doctor
    hedger backtest --strategy sma_crossover --symbols SPY,QQQ --days 365
    hedger tick                # paper, one cycle
    hedger serve               # paper, scheduled, with overnight reflection
"""

from datetime import datetime, timedelta, timezone

from hedger import backtest_simple
from hedger.base import AssetClass, Symbol
from hedger.data.sources import make_source
from hedger.strategies import get


def main():
    src = make_source("yfinance")
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=365)

    bars = {}
    for ticker in ("SPY", "QQQ"):
        sym = Symbol(ticker=ticker, asset_class=AssetClass.ETF)
        bars[sym] = list(src.bars(sym, start=start, end=end, timeframe="1d"))

    strategy = get("sma_crossover")
    res = backtest_simple(strategy, bars, fee_bps=5, slippage_bps=2)
    print(res.summary())


if __name__ == "__main__":
    main()
