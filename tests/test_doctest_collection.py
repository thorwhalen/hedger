"""Guards that CI actually collects the package's doctests.

wads CI runs ``pytest --doctest-modules`` with **no path argument**, so what
gets collected is decided entirely by ``testpaths`` in ``pyproject.toml``. When
``testpaths`` lists only ``tests``, the package directory is never visited and
every doctest in ``hedger/*.py`` is silently skipped -- a green CI that proves
nothing about the documented examples.

These tests fence that config against a regression, from both ends: the static
declaration in ``pyproject.toml``, and the collection pytest actually performs
when handed the CI invocation.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

#: Directory holding the importable package -- the one whose doctests must run.
PACKAGE_DIR_NAME = "hedger"

#: Repo root, as seen from ``tests/``. Only meaningful in a source checkout.
REPO_ROOT = Path(__file__).resolve().parent.parent

#: The flags wads CI appends to ``pytest``. Mirrors the ``run-tests-uv`` action
#: (plus this repo's ``[tool.wads.ci.testing]`` exclude_paths); no path argument
#: is passed, which is precisely the condition under test.
CI_PYTEST_ARGS = (
    "--doctest-modules",
    "-o",
    "doctest_optionflags=ELLIPSIS IGNORE_EXCEPTION_DETAIL",
    "--ignore=examples",
    "--ignore=scrap",
)


def _pyproject():
    """Parsed ``pyproject.toml``, or skip if we're not in a source checkout."""
    path = REPO_ROOT / "pyproject.toml"
    if not path.is_file():
        pytest.skip("not running from a source checkout")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_testpaths_includes_the_package_dir():
    """``testpaths`` must name the package dir, else CI collects no doctests."""
    ini = _pyproject()["tool"]["pytest"]["ini_options"]
    testpaths = ini["testpaths"]
    assert PACKAGE_DIR_NAME in testpaths, (
        f"{PACKAGE_DIR_NAME!r} missing from testpaths={testpaths!r}: wads CI runs "
        "`pytest --doctest-modules` with no path argument, so the package "
        "doctests would never be collected."
    )


def test_ci_invocation_collects_package_doctests():
    """The real CI command must collect doctests from ``hedger/``, not just ``tests/``."""
    if not (REPO_ROOT / PACKAGE_DIR_NAME).is_dir():
        pytest.skip("not running from a source checkout")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *CI_PYTEST_ARGS, "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    # pytest spells node ids with the platform separator, so accept either.
    prefixes = (f"{PACKAGE_DIR_NAME}/", f"{PACKAGE_DIR_NAME}\\")
    collected = [line for line in completed.stdout.splitlines() if line.startswith(prefixes)]
    assert collected, (
        "no doctest was collected from "
        f"{PACKAGE_DIR_NAME}/ under the CI invocation:\n{completed.stdout}"
    )
