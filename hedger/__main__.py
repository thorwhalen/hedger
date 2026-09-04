"""hedger CLI entry point.

    hedger doctor                          # env check
    hedger list-strategies                 # plug-ins
    hedger fetch SPY --days 30             # data sanity
    hedger backtest --strategy sma_crossover --symbols SPY,QQQ
    hedger tick                            # one paper tick
    hedger serve                           # block forever; runs reflection too
    hedger reflect --dry-run               # run reflection now

Following Thor's package architecture conventions: `cw` for dispatch, with
`hedger/tools.py::_dispatch_funcs` as the SSOT for what the CLI exposes.
"""

from __future__ import annotations


def main():
    # Auto-load hedger's canonical envfile into os.environ (override=False) so
    # interactive CLI use mirrors the systemd EnvironmentFile= behaviour. Only
    # happens on CLI invocation, never on `import hedger`.
    from hedger.install import (
        load_envfile_into_environ,
        warn_if_ambient_shadows_envfile,
    )

    # Warn BEFORE loading: load is override=False, so any ambient ALPACA_* vars
    # win and could point the CLI at a different account than `hedger serve`.
    warn_if_ambient_shadows_envfile()
    load_envfile_into_environ()

    import cw

    from hedger.tools import _dispatch_funcs as tools_funcs

    # cw.mk_parser + cw.run rather than cw.dispatch, because this used to be a
    # hand-built ArghParser. cw.run is what fires argcomplete (mk_parser does
    # not), which is why the hand-written `try: import argcomplete` block that
    # used to live here could simply be deleted rather than ported.
    parser = cw.mk_parser(tools_funcs)
    raise SystemExit(cw.run(parser))


if __name__ == "__main__":
    main()
