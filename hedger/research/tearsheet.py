"""Tear-sheet generation for end-of-cycle review.

Two backends, both optional:

* ``html_tearsheet`` — quantstats' HTML report. The simplest path: one
  function call, one self-contained .html file you can open in a browser.
* ``pyfolio_full_tearsheet`` — pyfolio's matplotlib panels. Heavier; only
  use it from a notebook or when you specifically want pyfolio's layout.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from hedger.research._optional import require


def _to_returns(nav: pd.Series) -> pd.Series:
    nav = pd.Series(nav).astype(float).sort_index()
    return nav.pct_change().dropna()


def html_tearsheet(
    nav: pd.Series,
    output_path: str | Path,
    *,
    title: str = "hedger strategy",
    benchmark: pd.Series | None = None,
) -> Path:
    """Render a self-contained HTML tear sheet via quantstats. Returns the path.

    ``benchmark`` is optional; when supplied, it is converted to returns the
    same way as ``nav`` and used for the alpha/beta panels.
    """
    qs = require("quantstats", extra="research")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    returns = _to_returns(nav)
    bench_returns = _to_returns(benchmark) if benchmark is not None else None
    qs.reports.html(
        returns=returns,
        benchmark=bench_returns,
        title=title,
        output=str(out),
    )
    return out


def pyfolio_full_tearsheet(
    nav: pd.Series,
    *,
    benchmark: pd.Series | None = None,
):
    """Run pyfolio's ``create_full_tear_sheet`` against a NAV series.

    Returns ``None`` (pyfolio renders into the active matplotlib figure).
    Call from a notebook; from a script you'd usually want ``html_tearsheet``.
    """
    pf = require("pyfolio", extra="research")
    returns = _to_returns(nav)
    bench_returns = _to_returns(benchmark) if benchmark is not None else None
    return pf.create_full_tear_sheet(returns, benchmark_rets=bench_returns)
