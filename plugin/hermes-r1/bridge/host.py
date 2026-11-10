"""Host contract: everything the bridge needs from a Hermes install.

Two implementations exist: ``MockHost`` (scripted, no Hermes required) and
``HermesHost`` (the real thing). ``server.py`` and ``turns.py`` only ever talk to
this interface. Do not change these signatures without updating docs/SPEC.md.

All dict shapes are the JSON shapes from docs/SPEC.md section 3.2, minus the
fields the bridge adds itself:

* ``Bot``      - the host returns everything except ``state`` / ``active`` /
                 ``last`` freshness, which the bridge overlays from its own
                 running turns. ``avatar.url`` and ``pet.url`` are left as ""
                 by the host; the bridge fills in signed URLs.
* ``TurnEvent``- the host yields events WITHOUT ``seq``; the bridge numbers them.
                 The ``speech`` event is produced by the bridge, never the host.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Protocol, runtime_checkable

Json = dict[str, Any]


class HostError(Exception):
    """An error with a stable machine code, mapped to an HTTP error by the server."""

    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class AssetFile:
    """A file on disk the bridge may serve (avatar image, pet spritesheet)."""

    path: Path
    content_type: str


@dataclass
class TurnHandle:
    """A running turn. ``events`` yields SPEC TurnEvent dicts without ``seq``.

    The iterator MUST end with exactly one ``{"t": "end", ...}`` event, also on
    failure and after ``stop()``; it must never raise.
    """

    session_id: str
    events: AsyncIterator[Json]


@runtime_checkable
class Host(Protocol):
    #: True for MockHost. Reported in /health and /bootstrap.
    mock: bool

    async def start(self) -> None:
        """Open network clients. Called once before serving."""

    async def close(self) -> None:
        """Release resources. Called once on shutdown."""

    async def list_bots(self) -> list[Json]:
        """All profiles as SPEC ``Bot`` dicts (see module docstring for omitted fields).

        Must be cheap enough to call every few seconds (cache disk reads ~5 s).
        """

    async def avatar_file(self, bot_id: str) -> AssetFile | None:
        """The uploaded avatar image for a bot, or None."""

    async def pet_file(self, bot_id: str) -> AssetFile | None:
        """The pet spritesheet for a bot, or None."""

    async def list_sessions(self, bot_id: str, limit: int) -> list[Json]:
        """SPEC ``Session`` dicts, newest first. Raises HostError('bot_locked', ..., 409) if unreachable."""

    async def get_messages(self, bot_id: str, session_id: str, limit: int) -> list[Json]:
        """SPEC ``Message`` dicts, oldest to newest, at most ``limit`` (the most recent ones)."""

    async def run_turn(self, bot_id: str, session_id: str | None, text: str) -> TurnHandle:
        """Start one agent turn. Creates a session when ``session_id`` is None.

        Raises HostError before returning if the turn cannot start at all
        (``bot_locked`` 409, ``upstream_unavailable`` 502). After it returns,
        failures are reported as an ``end`` event with status ``failed``.
        """

    async def stop_turn(self, bot_id: str, session_id: str) -> None:
        """Ask the running turn in this session to stop. No-op if none."""

    async def respond(self, bot_id: str, session_id: str, request_id: str,
                      *, choice: str | None = None, answer: str | None = None) -> None:
        """Resolve a pending ``ask`` (approval ``choice`` or clarify ``answer``)."""

    def voice_capabilities(self) -> Json:
        """``{"stt": bool, "tts": bool}``."""

    async def transcribe(self, audio_path: Path, content_type: str) -> str:
        """Speech to text. Raises HostError('stt_unavailable', ..., 501) when unsupported."""

    async def synthesize(self, text: str, out_dir: Path) -> AssetFile:
        """Text to speech into a new file inside ``out_dir``. Raises HostError('tts_unavailable', ..., 501)."""
