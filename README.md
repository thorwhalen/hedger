# hedger
Semantic Finance.

To install:	```pip install hedger```

# Examples

## get a small set of tickers (offline, from a local file)

```python
from hedger import get_ticker_symbols
tickers = get_ticker_symbols()
len(tickers)
# 4039
'GOOG' in tickers
# True
```
