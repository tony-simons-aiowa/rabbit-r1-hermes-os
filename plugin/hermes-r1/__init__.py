"""hermes-r1: Rabbit R1 bridge plugin for Hermes Agent.

Registers:

* ``hermes r1 ...`` (``serve``, ``status``, ``doctor``, ``setup``), implemented in ``cli.py``;
* the ``r1_notify`` tool, which appends a notification card to ``<state dir>/inbox.jsonl``.
  The bridge tails that file and shows the card on the R1.

Nothing heavy is imported at module load: this file is loaded as the package root by tests
and by Hermes itself, so aiohttp and the bridge submodules are only imported on demand.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

__version__ = "0.1.0"

logger = logging.getLogger(__name__)

R1_NOTIFY_SCHEMA: dict[str, Any] = {
    "name": "r1_notify",
    "description": (
        "Send a short notification card to the user's Rabbit R1 handheld. Use it for results the "
        "user should see away from the chat, such as a finished long task or something that needs "
        "attention. Keep it brief."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 60, "description": "Short headline (at most 60 characters)."},
            "body": {"type": "string", "maxLength": 400, "description": "Optional detail (at most 400 characters)."},
            "level": {
                "type": "string",
                "enum": ["info", "success", "warn"],
                "default": "info",
                "description": "Card style.",
            },
        },
        "required": ["title"],
    },
}

_LEVELS = ("info", "success", "warn")


def _state_dir() -> Path:
    """``$HERMES_R1_HOME``, else ``$HERMES_HOME/r1``, else ``~/.hermes/r1``."""
    explicit = os.environ.get("HERMES_R1_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        return Path(hermes_home).expanduser() / "r1"
    return Path.home() / ".hermes" / "r1"


def _profile_name() -> str:
    """The Hermes profile this process runs for, from the environment; "" when unknown.

    Hermes exposes it as ``HERMES_PROFILE_NAME`` / ``HERMES_PROFILE`` (see
    ``hermes_cli/profiles.py`` ``current_profile_name``), or implicitly as the parent directory of
    ``HERMES_HOME`` when that is ``.../profiles/<name>``. "" means the root (default) profile.
    """
    for name in ("HERMES_PROFILE_NAME", "HERMES_PROFILE"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        home = Path(hermes_home).expanduser()
        if home.parent.name == "profiles":
            return home.name
    return ""


def _r1_notify_handler(args: dict[str, Any] | None, **_: Any) -> str:
    """Tool handler: append one card to the inbox file and return a JSON result string."""
    args = args if isinstance(args, dict) else {}
    title = str(args.get("title") or "").strip()[:60]
    if not title:
        return json.dumps({"success": False, "error": "title is required"})
    body = str(args.get("body") or "").strip()[:400]
    level = str(args.get("level") or "info").strip().lower()
    if level not in _LEVELS:
        level = "info"
    record = {"ts": time.time(), "bot": _profile_name(), "title": title, "body": body, "level": level}
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        state = _state_dir()
        created = not state.exists()
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        if created:
            os.chmod(state, 0o700)
        fd = os.open(state / "inbox.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as exc:
        logger.warning("r1_notify could not write the inbox: %s", type(exc).__name__)
        return json.dumps({"success": False, "error": f"could not write inbox: {type(exc).__name__}"})
    return json.dumps({"success": True})


def _cli_setup(parser: Any) -> None:
    from . import cli

    cli.setup_parser(parser)


def _cli_handle(args: Any) -> int:
    from . import cli

    return cli.handle(args)


def register(ctx: Any) -> None:
    """Hermes plugin entry point. Each registration is isolated so one failure cannot block the other."""
    try:
        ctx.register_cli_command(
            name="r1",
            help="Rabbit R1 bridge: serve, pair, setup, doctor",
            description="Run the Rabbit R1 bridge and check or configure its link to Hermes.",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
        )
    except Exception:
        logger.exception("hermes-r1: could not register the `r1` CLI command")

    try:
        ctx.register_tool(
            name="r1_notify",
            toolset="r1",
            schema=R1_NOTIFY_SCHEMA,
            handler=_r1_notify_handler,
            description="Push a notification card to the user's Rabbit R1",
            emoji="\N{BELL}",
        )
    except Exception:
        logger.exception("hermes-r1: could not register the r1_notify tool")
