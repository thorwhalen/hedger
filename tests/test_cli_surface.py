"""Characterization tests for the ``hedger`` command-line surface.

Written during the argh -> ``cw`` migration. Every assertion was recorded from
the argh implementation *before* the swap, so this file fences the published
grammar rather than describing the new one.

Subprocess cases drive ``python -m hedger.tools`` rather than ``python -m
hedger``, deliberately: ``hedger/__main__.py``'s ``main()`` loads the canonical
envfile into ``os.environ`` and warns about ambient shadowing before it
dispatches. That is the right thing for an interactive CLI and the wrong thing
for a test, and the parser it then builds is the same one either way.
"""

import os
import subprocess
import sys

import pytest

import cw
from hedger.tools import _dispatch_funcs


#: Command names as the CLI spells them — underscores hyphenated, which is what
#: makes `where_keys` reachable as `hedger where-keys`.
COMMANDS = (
    "doctor",
    "install",
    "where-keys",
    "list-strategies",
    "fetch",
    "backtest",
    "tick",
    "serve",
    "reflect",
    "brief",
    "status",
)


@pytest.fixture(scope="module")
def parser():
    """The very parser ``hedger/__main__.py`` builds."""
    return cw.mk_parser(_dispatch_funcs)


#: Pins that make recorded text reproducible. Merged ONTO ``os.environ``, never
#: substituted for it: a replaced environment loses ``SYSTEMROOT`` on Windows and
#: the child interpreter dies in ``_Py_HashRandomization_Init`` before it runs a
#: line of our code.
ENV_PINS = {"COLUMNS": "100", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def run_cli(*argv):
    """Run ``python -m hedger.tools`` in a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, "-m", "hedger.tools", *argv],
        capture_output=True,
        text=True,
        env=dict(os.environ, **ENV_PINS),
        timeout=300,
    )


def usage_of(text):
    """The ``usage:`` block, whitespace-collapsed — width-independent."""
    lines = []
    for line in text.splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines)


class TestCommandNames:
    def test_every_command_is_reachable_under_its_published_name(self, parser, capsys):
        """`hedger install` and `hedger where-keys`, not `install_cmd` / `where_keys`.

        ``tools.py`` defines thin ``install`` / ``where_keys`` aliases precisely
        so the CLI spells them this way, without the importer-name collision
        that a bare ``from hedger.install import install`` would cause.
        """
        usage = usage_of(parser.format_usage())
        for name in COMMANDS:
            assert name in usage

    def test_underscored_spellings_are_not_accepted(self):
        """argh hyphenated; so does cw. `where_keys` was never a command."""
        proc = run_cli("where_keys")
        assert proc.returncode == 2
        assert "invalid choice" in proc.stderr


class TestGrammar:
    def test_top_level_help_lists_every_command(self):
        proc = run_cli("--help")
        assert proc.returncode == 0
        for name in COMMANDS:
            assert name in proc.stdout

    @pytest.mark.parametrize("command", COMMANDS)
    def test_each_subcommand_has_help(self, parser, command):
        action = parser._subparsers._group_actions[0]
        assert command in action.choices
        assert action.choices[command].format_help()

    def test_fetch_takes_a_required_positional_symbol(self, parser):
        sub = parser._subparsers._group_actions[0].choices["fetch"]
        usage = usage_of(sub.format_usage())
        assert usage.endswith(" symbol")
        assert "--symbol" not in usage

    def test_backtest_suppresses_short_flags_that_collide(self, parser):
        """``strategy``/``symbols``/``source`` all start with ``s``, so none gets ``-s``.

        argh infers a short flag from the first character and drops it when two
        parameters would claim the same one. ``-t``/``-d`` survive because
        ``timeframe`` and ``days`` are unique.
        """
        sub = parser._subparsers._group_actions[0].choices["backtest"]
        usage = usage_of(sub.format_usage())
        assert "-s " not in usage
        for fragment in (
            "--strategy STRATEGY",
            "--symbols SYMBOLS",
            "--source SOURCE",
            "-t TIMEFRAME",
            "-d DAYS",
        ):
            assert fragment in usage


class TestExitCodes:
    def test_no_arguments_prints_usage_to_stdout_and_exits_zero(self):
        """argh's behaviour, which plain argparse does NOT reproduce."""
        proc = run_cli()
        assert proc.returncode == 0
        assert proc.stdout.startswith("usage:")
        assert proc.stderr == ""

    def test_unknown_command_exits_two(self):
        proc = run_cli("no-such-command")
        assert proc.returncode == 2
        assert "invalid choice" in proc.stderr

    def test_unknown_flag_exits_two(self):
        proc = run_cli("doctor", "--no-such-flag")
        assert proc.returncode == 2

    def test_missing_required_positional_exits_two(self):
        proc = run_cli("fetch")
        assert proc.returncode == 2

    def test_bad_int_option_value_exits_two(self):
        """``--days`` is typed ``int`` by inference from its default."""
        proc = run_cli("fetch", "SPY", "--days", "abc")
        assert proc.returncode == 2
        assert "invalid int value" in proc.stderr

    def test_dispatch_returns_the_code_rather_than_exiting(self):
        """``cw.dispatch`` RETURNS where argh exited.

        Both entry points wrap it in ``raise SystemExit(...)``; without that,
        every argument error would exit 0 — invisible to every other test.
        """
        assert cw.dispatch(_dispatch_funcs, ["no-such-command"], standalone=True) == 2
