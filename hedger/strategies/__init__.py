"""Strategy registry — open/closed plugin architecture.

To add a strategy: drop a module in `hedger/strategies/`, decorate the
callable with `@register('my_strategy_name')`. Discovery via `available()`
or by name via `get('my_strategy_name')`.

This is what lets the overnight reflection loop `git add` a new strategy
file and have it picked up on the next run with no orchestration changes.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from hedger.base import Strategy

_REGISTRY: dict[str, Strategy] = {}


def register(name: str) -> Callable[[Strategy], Strategy]:
    """Decorator: register a strategy under `name`.

    >>> @register('demo_noop')
    ... def demo(bars, *, context=None): return ()
    >>> 'demo_noop' in available()
    True
    """

    def wrap(strat: Strategy) -> Strategy:
        if not hasattr(strat, "name"):
            try:
                strat.name = name  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass
        _REGISTRY[name] = strat
        return strat

    return wrap


def get(name: str) -> Strategy:
    """Look up a registered strategy by name."""
    if name not in _REGISTRY:
        _autoload()
    if name not in _REGISTRY:
        raise KeyError(f"Strategy {name!r} not found. Registered: {list(_REGISTRY)}")
    return _REGISTRY[name]


def available() -> list[str]:
    """List currently registered strategies (autoloads built-ins on first call)."""
    _autoload()
    return sorted(_REGISTRY)


def _autoload() -> None:
    """Import every submodule of `hedger.strategies` so their decorators run."""
    pkg = importlib.import_module(__name__)
    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name.startswith("_") or mod.name == "base":
            continue
        importlib.import_module(f"{__name__}.{mod.name}")


__all__ = ["register", "get", "available"]
