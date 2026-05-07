"""Set up a long-running hedger deployment safely.

Two CLI commands are exported:

- ``hedger install``     create the secrets envfile (mode 600) and, with
                       ``--systemd``, a systemd unit. Idempotent.
- ``hedger where-keys``  print where the envfile is, which keys are present
                       vs. missing, and the command to edit it.

Conventions:

- Secrets live in an envfile with mode 600, never in ``config.toml`` and
  never committed. Default path is ``/etc/hedger.env`` when running as root,
  otherwise ``$XDG_CONFIG_HOME/hedger/hedger.env`` (per-user systemd).
- The systemd unit reads that file via ``EnvironmentFile=``, so a restart
  is enough to rotate keys.
- No paths from the developer's machine are baked in: ``shutil.which("hedger")``
  and ``os.getcwd()`` resolve at install time.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

# Secrets we know about. Adding to this tuple makes the key appear in the
# envfile template and in `hedger where-keys` output.
KNOWN_SECRETS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
)

# TOML keys (lowercased) matching these patterns are refused by the config
# loader  it almost certainly means a user pasted a secret into config.toml.
FORBIDDEN_TOML_SUFFIXES: tuple[str, ...] = (
    "_key", "_secret", "_token", "_password",
)
FORBIDDEN_TOML_PREFIXES: tuple[str, ...] = (
    "anthropic_", "alpaca_",
)


def is_secret_key_name(name: str) -> bool:
    """True if *name* looks like a secret and must not appear in config.toml.

    >>> is_secret_key_name("anthropic_api_key")
    True
    >>> is_secret_key_name("ALPACA_SECRET_KEY")
    True
    >>> is_secret_key_name("timeframe")
    False
    """
    n = name.lower()
    return (
        any(n.endswith(s) for s in FORBIDDEN_TOML_SUFFIXES)
        or any(n.startswith(p) for p in FORBIDDEN_TOML_PREFIXES)
    )


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def default_envfile() -> Path:
    """Return the default envfile path for the current user.

    Root: ``/etc/hedger.env``. Non-root: ``$XDG_CONFIG_HOME/hedger/hedger.env``.
    """
    if _is_root():
        return Path("/etc/hedger.env")
    return _xdg_config_home() / "hedger" / "hedger.env"


def resolve_envfile(explicit: str | os.PathLike | None = None) -> Path:
    """Resolve the envfile path that the runtime would actually use.

    Order: *explicit* arg, then ``HEDGER_ENVFILE`` env var, then
    :func:`default_envfile`. Used by both the auto-loader and
    ``hedger where-keys`` so they always agree.
    """
    if explicit:
        return Path(explicit)
    if os.environ.get("HEDGER_ENVFILE"):
        return Path(os.environ["HEDGER_ENVFILE"])
    return default_envfile()


def default_unit_path() -> Path:
    """Return the default systemd unit path for the current user."""
    if _is_root():
        return Path("/etc/systemd/system/hedger.service")
    return _xdg_config_home() / "systemd" / "user" / "hedger.service"


def _editor_cmd(path: Path) -> str:
    """Return the shell command the user should run to edit *path*.

    Uses ``$EDITOR`` if set, else ``pico``. Prefixes ``sudo`` when the current
    user can't write the file (or its parent, if the file doesn't exist yet).
    """
    editor = os.environ.get("EDITOR", "pico")
    if path.exists():
        needs_sudo = not os.access(path, os.W_OK)
    else:
        needs_sudo = path.parent.exists() and not os.access(path.parent, os.W_OK)
    return f"{'sudo ' if needs_sudo else ''}{editor} {path}"


def _envfile_template() -> str:
    lines = [
        "# hedger secrets  keep mode 600, never commit, never put in config.toml.",
        "# Created by `hedger install`. Edit values, then start/restart hedger.",
        "",
    ]
    lines.extend(f"{name}=" for name in KNOWN_SECRETS)
    lines.append("")
    return "\n".join(lines)


def _present_secrets(envfile: Path) -> set[str]:
    """Return KNOWN_SECRETS that have a non-empty value in *envfile*."""
    if not envfile.exists():
        return set()
    present: set[str] = set()
    for raw in envfile.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() in KNOWN_SECRETS and v.strip().strip('"').strip("'"):
            present.add(k.strip())
    return present


def _create_envfile(path: Path) -> bool:
    """Create *path* with the template if missing. Always enforce mode 600.

    Returns True if newly created. Existing content is never modified.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    if created:
        # O_EXCL guards against a race; 0o600 is set at creation, not after.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(_envfile_template())
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return created


def _hedger_binary() -> str:
    """Resolve a runnable hedger command for the systemd unit.

    Prefers a ``hedger`` on PATH; falls back to ``<python> -m hedger`` so the
    unit still works in venvs without console-script shims.
    """
    found = shutil.which("hedger")
    return found if found else f"{sys.executable} -m hedger"


def _systemd_unit_text(envfile: Path, workdir: Path) -> str:
    install_target = "default.target" if not _is_root() else "multi-user.target"
    return (
        "[Unit]\n"
        "Description=hedger trading scheduler\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=exec\n"
        f"WorkingDirectory={workdir}\n"
        f"EnvironmentFile={envfile}\n"
        f"ExecStart={_hedger_binary()} serve\n"
        "Restart=on-failure\n"
        "RestartSec=10\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        f"WantedBy={install_target}\n"
    )


def _create_unit(path: Path, envfile: Path, workdir: Path) -> bool:
    """Create the systemd unit file if missing. Never overwrites."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    path.write_text(_systemd_unit_text(envfile, workdir))
    return True


def install(
    *,
    systemd: bool = False,
    envfile: str | None = None,
    workdir: str | None = None,
) -> str:
    """Set up hedger for a long-running deployment. Idempotent.

    Creates the secrets envfile (mode 600) and, with ``--systemd``, a
    systemd unit. Never overwrites existing files. Prints the next steps,
    including the exact command to edit the envfile.
    """
    env_path = Path(envfile) if envfile else default_envfile()
    work_path = Path(workdir) if workdir else Path.cwd()

    out: list[str] = []
    created = _create_envfile(env_path)
    out.append(f"envfile: {'created' if created else 'present'} at {env_path} (mode 600)")

    unit_path: Path | None = None
    if systemd:
        unit_path = default_unit_path()
        u_created = _create_unit(unit_path, env_path, work_path)
        out.append(f"systemd: {'created' if u_created else 'present'} at {unit_path}")

    missing = [s for s in KNOWN_SECRETS if s not in _present_secrets(env_path)]

    out.append("")
    out.append("Next steps:")
    out.append(f"  1. Edit secrets:  {_editor_cmd(env_path)}")
    if missing:
        out.append(f"     missing: {', '.join(missing)}")
    if systemd:
        scope = "" if _is_root() else "--user "
        out.append(f"  2. Activate:      systemctl {scope}daemon-reload "
                   f"&& systemctl {scope}enable --now hedger.service")
        out.append(f"  3. Watch logs:    journalctl {scope}-u hedger.service -f")
    else:
        out.append("  2. (Optional) Re-run with --systemd to also create a systemd unit.")
    return "\n".join(out)


def where_keys(*, envfile: str | None = None) -> str:
    """Print where hedger expects its secrets and which are set.

    Honours ``--envfile`` and ``HEDGER_ENVFILE`` so the path shown matches
    the one the auto-loader would read.
    """
    env_path = resolve_envfile(envfile)
    out: list[str] = [f"envfile: {env_path}"]

    if not env_path.exists():
        out.append("status:  not created yet  run `hedger install`")
        out.append(f"missing: {', '.join(KNOWN_SECRETS)}")
        return "\n".join(out)

    mode = stat.S_IMODE(env_path.stat().st_mode)
    out.append(f"mode:    {mode:o} ({'OK' if mode == 0o600 else 'WARN  should be 600'})")
    present = _present_secrets(env_path)
    missing = [s for s in KNOWN_SECRETS if s not in present]
    out.append(f"set:     {', '.join(sorted(present)) or '(none)'}")
    out.append(f"missing: {', '.join(missing) or '(none)'}")
    out.append(f"edit:    {_editor_cmd(env_path)}")
    return "\n".join(out)


def _parse_envfile(text: str) -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` envfile. Strips matched surrounding quotes.

    Lines starting with ``#`` (after stripping whitespace) and blank lines are
    ignored. Lines without ``=`` are ignored. Empty values are skipped so an
    unset placeholder doesn't shadow a real env var.

    >>> sorted(_parse_envfile('A=1\\n# c\\nB="two"\\nC=\\n').items())
    [('A', '1'), ('B', 'two')]
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if (len(v) >= 2) and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k and v:
            out[k] = v
    return out


def load_envfile_into_environ(path: str | os.PathLike | None = None) -> dict[str, str]:
    """Load hedger's canonical envfile into ``os.environ`` (override=False).

    Resolution order:

    1. *path* argument
    2. ``HEDGER_ENVFILE`` env var
    3. :func:`default_envfile`

    Existing env vars are *never* overwritten — so a systemd
    ``EnvironmentFile=`` (or anything you `export`'d) still wins. Returns the
    dict of keys that were actually set by this call (i.e. not pre-existing).

    Prints a one-line warning to stderr if the file's mode is laxer than 600.
    """
    target = resolve_envfile(path)
    if not target.is_file():
        return {}

    mode = stat.S_IMODE(target.stat().st_mode)
    if mode & 0o077:
        sys.stderr.write(
            f"hedger: warning: {target} mode is {mode:o}; should be 600. "
            f"Run `chmod 600 {target}`.\n"
        )

    parsed = _parse_envfile(target.read_text())
    applied: dict[str, str] = {}
    for k, v in parsed.items():
        if k not in os.environ:
            os.environ[k] = v
            applied[k] = v
    return applied


__all__ = [
    "install",
    "where_keys",
    "is_secret_key_name",
    "default_envfile",
    "default_unit_path",
    "resolve_envfile",
    "load_envfile_into_environ",
    "KNOWN_SECRETS",
    "FORBIDDEN_TOML_SUFFIXES",
    "FORBIDDEN_TOML_PREFIXES",
]
