"""Helper for optional research-stack imports.

Each public facade in ``hedger.research`` calls ``require(...)`` with the
import path of its backing library. If the library is missing, we raise
ImportError with a precise install hint pointing at the ``hedger[research]``
extra. This keeps hedger core dependency-free of the heavyweight research
libs while giving the reflection cycle a clear, single-line failure mode.
"""

from __future__ import annotations

import importlib
from types import ModuleType


def require(modname: str, *, extra: str = "research") -> ModuleType:
    """Import ``modname`` or raise a precise ImportError pointing at ``extra``.

    >>> m = require('json')               # stdlib always present
    >>> m.__name__
    'json'
    >>> require('definitely_not_a_real_module_xyz')   # doctest: +ELLIPSIS
    Traceback (most recent call last):
    ...
    ImportError: hedger.research needs `definitely_not_a_real_module_xyz`...
    """
    try:
        return importlib.import_module(modname)
    except ImportError as exc:
        raise ImportError(
            f"hedger.research needs `{modname}`, which isn't installed. "
            f"Install the research extras: `pip install -e .[{extra}]` "
            f"(or `pip install {modname}` for just this one)."
        ) from exc
