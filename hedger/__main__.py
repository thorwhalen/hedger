"""hedger CLI entry point.

    hedger doctor                          # env check
    hedger list-strategies                 # plug-ins
    hedger fetch SPY --days 30             # data sanity
    hedger backtest --strategy sma_crossover --symbols SPY,QQQ
    hedger tick                            # one paper tick
    hedger serve                           # block forever; runs reflection too
    hedger reflect --dry-run               # run reflection now

Following Thor's package architecture conventions: `argh` for dispatch,
namespaced sub-commands ("tools" namespace) for module-level helpers.
"""

from __future__ import annotations


def dispatch_with_namespaces(functions, namespaced_funcs=None):
    """argh dispatch helper with optional namespaces and tab-completion."""
    import argh

    parser = argh.ArghParser()
    parser.add_commands(functions)
    if namespaced_funcs:
        for namespace, funcs in namespaced_funcs.items():
            parser.add_commands(funcs, namespace=namespace)
    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    parser.dispatch()


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

    from hedger.tools import _dispatch_funcs as tools_funcs

    dispatch_with_namespaces(tools_funcs)


if __name__ == "__main__":
    main()
