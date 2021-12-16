"""Get tickers related to some query by searching the query on the web,
and harvesting ticker symbols from that crawl
"""


from functools import partial
from collections import Counter

from hedger.util import get_ticker_symbols
from guise.tools import google_results_toks

DFLT_TICKERS = set(get_ticker_symbols())

my_google_results_toks = partial(google_results_toks, include=None, exclude=None)


def google_toks(query, url_to_html_kwargs=(('timeout', 10),)):
    url_to_html_kwargs = dict(url_to_html_kwargs or {})
    return list(my_google_results_toks(query, url_to_html_kwargs=url_to_html_kwargs))


def tickers_related_to(query, tickers=None):
    tickers = tickers or DFLT_TICKERS
    return Counter(filter(tickers.__contains__, google_toks(query)))
