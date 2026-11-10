"""``hermes r1 ...`` subcommands: serve, status, doctor, setup.

The pure logic (env parsing, profile discovery, checks, setup) takes ``hermes_home: Path`` so
tests can point it at a temporary directory. Secret values are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

DEFAULT_PORT = 8717
DEFAULT_HERMES_URL = "http://127.0.0.1:8642"
PLUGIN_DIR = Path(__file__).resolve().parent
STATIC_DIR = PLUGIN_DIR / "static"
_PROFILE_MARKERS = ("config.yaml", ".env", "SOUL.md", "profile.yaml")


# --------------------------------------------------------------------------- paths


def default_hermes_home() -> Path:
    """``$HERMES_HOME`` or ``~/.hermes``."""
    value = os.environ.get("HERMES_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".hermes"


def resolve_state_dir(hermes_home: Path | None = None) -> Path:
    """``$HERMES_R1_HOME``, else ``<hermes_home>/r1``, else ``~/.hermes/r1``."""
    explicit = os.environ.get("HERMES_R1_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if hermes_home is not None:
        return Path(hermes_home) / "r1"
    return default_hermes_home() / "r1"


def discover_named_profiles(hermes_home: Path) -> list[str]:
    """Names of profile directories under ``<hermes_home>/profiles`` (sorted)."""
    root = hermes_home / "profiles"
    if not root.is_dir():
        return []
    names = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in ("default", ".", ".."):
            continue
        if any((child / marker).is_file() for marker in _PROFILE_MARKERS):
            names.append(child.name)
    return names


def profile_env_path(hermes_home: Path, profile: str | None) -> Path:
    """``.env`` for the default profile (None) or a named profile."""
    if profile is None:
        return hermes_home / ".env"
    return hermes_home / "profiles" / profile / ".env"


def _api_key_present(env_path: Path) -> bool:
    from .bridge.hermes_host import read_env_value

    return read_env_value(env_path, "API_SERVER_KEY") is not None


def _read_config(hermes_home: Path) -> dict[str, Any]:
    path = hermes_home / "config.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- status


def read_state(state_dir: Path) -> dict[str, Any]:
    """``state.json`` as a dict, or {} when absent or unreadable."""
    try:
        data = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def bridge_running(port: int, timeout: float = 2.0) -> bool:
    """True when the bridge answers its health route on localhost."""
    url = f"http://127.0.0.1:{port}/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed loopback URL
            return response.status == 200
    except (OSError, ValueError):
        return False


def status_lines(state_dir: Path, port: int, probe: Callable[[int], bool] | None = None) -> list[str]:
    state = read_state(state_dir)
    devices = state.get("devices")
    device_count = len(devices) if isinstance(devices, dict) else 0
    public_url = state.get("public_url") if isinstance(state.get("public_url"), str) else ""
    running = (probe or bridge_running)(port)
    return [
        f"state dir:   {state_dir}",
        f"bridge:      {'running' if running else 'not running'} on http://127.0.0.1:{port}",
        f"public URL:  {public_url or '(not set)'}",
        f"devices:     {device_count}",
    ]


# --------------------------------------------------------------------------- doctor


@dataclass(frozen=True)
class Check:
    ok: bool
    text: str

    def line(self) -> str:
        return f"{'✓' if self.ok else '✗'} {self.text}"


def _hermes_reachable(hermes_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{hermes_url.rstrip('/')}/health", timeout=2.0) as response:  # noqa: S310
            return response.status == 200
    except (OSError, ValueError):
        return False


def doctor_checks(
    hermes_home: Path,
    hermes_url: str = DEFAULT_HERMES_URL,
    static_dir: Path = STATIC_DIR,
    *,
    probe: Callable[[str], bool] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> list[Check]:
    """Run the setup checks. Key values are never included in the output, only profile names.

    ``probe`` and ``which`` are looked up at call time so tests can replace them."""
    probe = probe or _hermes_reachable
    which = which or shutil.which
    checks: list[Check] = []
    reachable = probe(hermes_url)
    checks.append(Check(reachable, f"Hermes API server reachable at {hermes_url}/health"
                        if reachable else f"Hermes API server not reachable at {hermes_url}/health (is `hermes gateway` running?)"))

    default_env = profile_env_path(hermes_home, None)
    has_default = _api_key_present(default_env)
    checks.append(Check(has_default, "default profile has API_SERVER_KEY" if has_default
                        else f"default profile has no API_SERVER_KEY in {default_env}"))

    for name in discover_named_profiles(hermes_home):
        has_key = _api_key_present(profile_env_path(hermes_home, name))
        checks.append(Check(has_key, f"profile '{name}' has its own API_SERVER_KEY" if has_key
                            else f"profile '{name}' has no API_SERVER_KEY (run: hermes r1 setup)"))

    gateway = _read_config(hermes_home).get("gateway")
    multiplex = isinstance(gateway, dict) and gateway.get("multiplex_profiles") is True
    checks.append(Check(multiplex, "gateway.multiplex_profiles is true in config.yaml" if multiplex
                        else "gateway.multiplex_profiles is not true in config.yaml (named profiles need it)"))

    for binary, purpose in (("cloudflared", "--tunnel cloudflared"), ("tailscale", "--tunnel tailscale")):
        found = which(binary) is not None
        checks.append(Check(found, f"{binary} found on PATH ({purpose})" if found
                            else f"{binary} not found on PATH (only needed for {purpose})"))

    for app in ("app", "studio"):
        built = (static_dir / app / "index.html").is_file()
        checks.append(Check(built, f"static/{app} is built" if built
                            else f"static/{app} is not built (run the build in the repo)"))
    return checks


# --------------------------------------------------------------------------- setup


@dataclass(frozen=True)
class SetupItem:
    profile: str
    env_path: Path
    exists: bool


def plan_setup(hermes_home: Path) -> list[SetupItem]:
    """Named profiles that have no API_SERVER_KEY yet."""
    items = []
    for name in discover_named_profiles(hermes_home):
        env_path = profile_env_path(hermes_home, name)
        if not _api_key_present(env_path):
            items.append(SetupItem(name, env_path, env_path.exists()))
    return items


def _backup_path(env_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = env_path.with_name(f".env.bak-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = env_path.with_name(f".env.bak-{stamp}-{counter}")
        counter += 1
    return candidate


def _write_private(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def add_api_key(env_path: Path) -> Path | None:
    """Append a freshly generated ``API_SERVER_KEY`` to ``env_path``. Returns the backup path if the
    file existed. The generated value is never returned or printed."""
    value = secrets.token_urlsafe(32)
    line = f"API_SERVER_KEY={value}\n"
    if not env_path.exists():
        _write_private(env_path, line)
        return None
    backup = _backup_path(env_path)
    shutil.copy2(env_path, backup)
    os.chmod(backup, 0o600)
    existing = env_path.read_text(encoding="utf-8")
    if existing and not existing.endswith("\n"):
        existing += "\n"
    _write_private(env_path, existing + line)
    return backup


def run_setup(
    hermes_home: Path,
    *,
    yes: bool = False,
    dry_run: bool = False,
    confirm: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    items = plan_setup(hermes_home)
    if not items:
        out("All named profiles already have an API_SERVER_KEY. Nothing to do.")
    else:
        out("Profiles without an API_SERVER_KEY:")
        for item in items:
            out(f"  - {item.profile}: would add API_SERVER_KEY to {item.env_path}"
                + (" (after backing up the file)" if item.exists else " (creating the file, mode 0600)"))
    if not _api_key_present(profile_env_path(hermes_home, None)):
        out("Note: the default profile has no API_SERVER_KEY. Add one to "
            f"{profile_env_path(hermes_home, None)} yourself; this command does not change it.")
    if dry_run or not items:
        if dry_run:
            out("Dry run: no files were changed.")
        return 0
    if not yes:
        try:
            answer = confirm("Generate and write these keys? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            out("No changes made.")
            return 0
    updated = []
    for item in items:
        backup = add_api_key(item.env_path)
        updated.append(item.profile)
        if backup is not None:
            out(f"  backup of {item.env_path.name} for '{item.profile}': {backup}")
    out("Updated profiles: " + ", ".join(updated))
    out("Restart the gateway to apply: hermes gateway restart")
    return 0


# --------------------------------------------------------------------------- argparse


def setup_parser(parser: argparse.ArgumentParser) -> None:
    """Build the ``hermes r1`` subcommand tree."""
    parser.set_defaults(_r1_help=parser.print_help)
    sub = parser.add_subparsers(dest="r1_command", metavar="COMMAND")

    serve = sub.add_parser("serve", help="run the bridge in the foreground")
    serve.add_argument("--mock", action="store_true", help="use scripted bots (no Hermes needed)")
    serve.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    serve.add_argument("--port", type=int, default=None, help=f"bind port (default {DEFAULT_PORT})")
    serve.add_argument("--public-url", default=None, help="public HTTPS URL of the bridge")
    serve.add_argument("--tunnel", choices=["none", "cloudflared", "tailscale"], default=None)
    serve.add_argument("--allow-remote-admin", action="store_true")
    serve.add_argument("--hermes-url", default=None, help=f"Hermes API server (default {DEFAULT_HERMES_URL})")
    serve.add_argument("--hermes-home", default=None, help="Hermes home (default $HERMES_HOME or ~/.hermes)")
    serve.add_argument("--state-dir", default=None, help="bridge state directory")

    status = sub.add_parser("status", help="show whether the bridge is running")
    status.add_argument("--port", type=int, default=DEFAULT_PORT)
    status.add_argument("--hermes-home", default=None)
    status.add_argument("--state-dir", default=None)

    doctor = sub.add_parser("doctor", help="check the link to Hermes and the R1 setup")
    doctor.add_argument("--hermes-url", default=DEFAULT_HERMES_URL)
    doctor.add_argument("--hermes-home", default=None)

    setup = sub.add_parser("setup", help="generate missing API_SERVER_KEY values for named profiles")
    setup.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    setup.add_argument("--dry-run", action="store_true", help="only list what would change")
    setup.add_argument("--hermes-home", default=None)


def _hermes_home_arg(args: argparse.Namespace) -> Path:
    value = getattr(args, "hermes_home", None)
    return Path(value).expanduser() if value else default_hermes_home()


def _serve_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    if args.mock:
        argv.append("--mock")
    if args.allow_remote_admin:
        argv.append("--allow-remote-admin")
    for flag, value in (
        ("--host", args.host), ("--port", args.port), ("--public-url", args.public_url),
        ("--tunnel", args.tunnel), ("--hermes-url", args.hermes_url), ("--hermes-home", args.hermes_home),
        ("--state-dir", args.state_dir),
    ):
        if value is not None:
            argv.extend([flag, str(value)])
    return argv


def handle(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``hermes r1`` command. Returns the process exit code."""
    command = getattr(args, "r1_command", None)
    if command is None:
        help_fn = getattr(args, "_r1_help", None)
        if callable(help_fn):
            help_fn()
        return 0
    if command == "serve":
        from .bridge.__main__ import main as bridge_main

        return int(bridge_main(_serve_argv(args)) or 0)
    if command == "status":
        state_dir = Path(args.state_dir).expanduser() if args.state_dir else resolve_state_dir(_hermes_home_arg(args))
        for line in status_lines(state_dir, args.port):
            print(line)
        return 0
    if command == "doctor":
        failures = 0
        for check in doctor_checks(_hermes_home_arg(args), args.hermes_url, STATIC_DIR):
            print(check.line())
            failures += 0 if check.ok else 1
        return 0 if failures == 0 else 1
    if command == "setup":
        return run_setup(_hermes_home_arg(args), yes=args.yes, dry_run=args.dry_run,
                         confirm=input, out=lambda text: print(text, file=sys.stdout))
    print(f"Unknown command: {command}", file=sys.stderr)
    return 2
