"""TurnManager: runs host turns into per-turn event logs with long-poll waiters.

Each turn consumes the host's event iterator in a background task, numbers the
events (``seq`` from 1), tracks per-bot overlay state (busy / ask / last reply),
optionally synthesizes speech for the final answer, and guarantees exactly one
``end`` event per turn.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from .auth import signed_url
from .host import AssetFile, Host, HostError, Json, TurnHandle

log = logging.getLogger(__name__)

FINISHED_RETENTION = 600.0
RECENT_END_WINDOW = 90.0
AUDIO_RETENTION = 3600.0
MAX_WAIT = 25.0
END_STATUSES = ("completed", "failed", "cancelled")
MAX_TTS_CHARS = 2000


@dataclass
class Turn:
    id: str
    bot_id: str
    session_id: str
    device_id: str
    speak: bool
    record_finished: bool = True
    audio_base_path: str = ""
    events: list[Json] = field(default_factory=list)
    done: bool = False
    finishing: bool = False
    ended: float | None = None
    text_parts: list[str] = field(default_factory=list)
    pending_asks: dict[str, Json] = field(default_factory=dict)
    responding_asks: set[str] = field(default_factory=set)
    resolved_asks: set[str] = field(default_factory=set)
    waiters: list[asyncio.Future[None]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    handle: TurnHandle | None = None


class TurnManager:
    """Owns running and recently finished turns for one bridge."""

    def __init__(self, host: Host, store: Any, audio_dir: Path) -> None:
        self.host = host
        self.store = store
        self.audio_dir = Path(audio_dir)
        self._turns: dict[str, Turn] = {}
        self._starting: set[tuple[str, str]] = set()
        self._last: dict[str, dict[str, Any]] = {}
        self._bot_ended: dict[str, float] = {}
        self._audio: dict[str, tuple[AssetFile, float]] = {}

    # ---- lookup and pruning --------------------------------------------

    def _prune(self) -> None:
        now = time.time()
        for tid in [t.id for t in self._turns.values() if t.done and t.ended and now - t.ended > FINISHED_RETENTION]:
            del self._turns[tid]
        for aid in [a for a, (_, made) in self._audio.items() if now - made > AUDIO_RETENTION]:
            asset, _ = self._audio.pop(aid)
            try:
                asset.path.unlink(missing_ok=True)
            except OSError:
                log.warning("could not delete audio file %s", asset.path)

    def get(self, turn_id: str, *, device_id: str | None = None) -> Turn:
        self._prune()
        turn = self._turns.get(turn_id)
        if turn is None or (device_id is not None and turn.device_id != device_id):
            raise HostError("not_found", "Unknown turn.", 404)
        return turn

    def _running_for(self, device_id: str, bot_id: str) -> bool:
        return any(
            not t.done and t.device_id == device_id and t.bot_id == bot_id for t in self._turns.values()
        )

    # ---- starting ------------------------------------------------------

    async def start(
        self,
        device_id: str,
        bot_id: str,
        session_id: str | None,
        text: str,
        speak: bool,
        audio_base_path: str = "",
        record_finished: bool = True,
    ) -> Turn:
        self._prune()
        key = (device_id, bot_id)
        if key in self._starting or self._running_for(device_id, bot_id):
            raise HostError("busy", "A reply is already in progress for this bot.", 429)
        self._starting.add(key)
        try:
            handle = await self.host.run_turn(bot_id, session_id, text)
        finally:
            self._starting.discard(key)
        turn = Turn(
            id="t_" + secrets.token_hex(6),
            bot_id=bot_id,
            session_id=handle.session_id,
            device_id=device_id,
            speak=speak,
            record_finished=record_finished,
            audio_base_path=audio_base_path.rstrip("/"),
            handle=handle,
        )
        self._turns[turn.id] = turn
        turn.task = asyncio.create_task(self._consume(turn, handle.events))
        return turn

    # ---- event log -----------------------------------------------------

    def _notify(self, turn: Turn) -> None:
        waiters, turn.waiters = turn.waiters, []
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)

    def _append(self, turn: Turn, event: Json) -> None:
        if turn.done:
            return
        numbered = {"seq": len(turn.events) + 1, **{k: v for k, v in event.items() if k != "seq"}}
        turn.events.append(numbered)
        if numbered.get("t") == "end":
            turn.done = True
            turn.ended = time.time()
            turn.pending_asks.clear()
        self._notify(turn)

    async def wait_events(
        self,
        turn_id: str,
        after: int,
        wait: float,
        *,
        device_id: str | None = None,
    ) -> tuple[list[Json], int, bool]:
        """Long-poll: events with seq > after, waiting up to ``wait`` seconds if none."""
        turn = self.get(turn_id, device_id=device_id)
        wait = max(0.0, min(float(wait), MAX_WAIT))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            fresh = turn.events[max(0, after):]
            if fresh or turn.done:
                next_seq = fresh[-1]["seq"] if fresh else after
                return list(fresh), next_seq, turn.done
            remaining = deadline - loop.time()
            if remaining <= 0:
                return [], after, False
            fut: asyncio.Future[None] = loop.create_future()
            turn.waiters.append(fut)
            try:
                await asyncio.wait_for(fut, remaining)
            except asyncio.TimeoutError:
                pass
            finally:
                if fut in turn.waiters:
                    turn.waiters.remove(fut)

    # ---- consuming the host stream -------------------------------------

    async def _consume(self, turn: Turn, events: AsyncIterator[Json]) -> None:
        try:
            async for raw in events:
                await self._handle(turn, raw)
                if turn.done:
                    break
            if not turn.done:
                await self._finish(turn, "failed", "turn ended without an end event")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the host must never raise; be robust anyway
            log.exception("turn %s: host event stream failed", turn.id)
            if not turn.done:
                await self._finish(turn, "failed", str(exc) or type(exc).__name__)

    async def _handle(self, turn: Turn, raw: Json) -> None:
        kind = raw.get("t")
        if kind == "end":
            status = raw.get("status") if raw.get("status") in END_STATUSES else "failed"
            text = raw.get("text") if isinstance(raw.get("text"), str) else ""
            usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
            await self._finish(
                turn,
                status,
                str(raw.get("error") or ""),
                text=text or "".join(turn.text_parts),
                usage=usage,
            )
            return
        if kind == "delta" and isinstance(raw.get("text"), str):
            turn.text_parts.append(raw["text"])
        elif kind == "ask" and isinstance(raw.get("request_id"), str):
            request_id = raw["request_id"]
            # Keep the current prompt independently of the replayable event log.
            turn.pending_asks[request_id] = {
                "t": "ask", "request_id": request_id,
                "kind": raw.get("kind", "clarify"), "title": raw.get("title", "Question"),
                "detail": raw.get("detail", ""), "choices": raw.get("choices", []),
            }
        elif kind == "ask_done":
            if not self._resolve_ask(turn, raw.get("request_id")):
                return
        self._append(turn, raw)

    def _resolve_ask(self, turn: Turn, request_id: Any) -> bool:
        """Mark an ask resolved once; False when it was already resolved."""
        if not isinstance(request_id, str) or request_id in turn.resolved_asks:
            return False
        turn.resolved_asks.add(request_id)
        turn.pending_asks.pop(request_id, None)
        turn.responding_asks.discard(request_id)
        return True

    async def _finish(
        self,
        turn: Turn,
        status: str,
        error: str,
        *,
        text: str | None = None,
        usage: Json | None = None,
    ) -> None:
        if turn.done or turn.finishing:
            return
        turn.finishing = True
        try:
            final = text if text is not None else "".join(turn.text_parts)
            if status == "completed" and turn.speak and final.strip():
                speech = await self._speech_event(final, turn.audio_base_path)
                if speech is not None:
                    self._append(turn, speech)
            self._record_last(turn, status, final)
            self._append(turn, {
                "t": "end",
                "status": status,
                "text": final,
                "error": error,
                "usage": usage or {"input": 0, "output": 0},
            })
        finally:
            turn.finishing = False

    def _record_last(self, turn: Turn, status: str, text: str) -> None:
        now = time.time()
        self._bot_ended[turn.bot_id] = now
        # A one-line plain preview: drop markdown markers so the roster does not show "## Steps".
        one_line = " ".join(re.sub(r"```.*?```|[#*_`>]+", " ", text, flags=re.S).split())
        if status == "completed" and one_line:
            self._last[turn.bot_id] = {"text": one_line[:80], "ts": now}
        if not turn.record_finished:
            return
        entry = {
            "kind": "chat", "bot": turn.bot_id, "session_id": turn.session_id,
            "turn_id": turn.id, "status": status, "text": one_line[:240], "ts": now,
        }
        try:
            self.store.add_finished(turn.device_id, entry)
        except Exception:
            # Fleet persistence is ancillary; it must never interrupt a turn.
            log.warning("could not persist finished turn %s", turn.id, exc_info=True)

    async def _speech_event(self, text: str, base_path: str = "") -> Json | None:
        """Synthesize ``text``; returns a ``speech`` event or None. Never raises."""
        try:
            caps = self.host.voice_capabilities()
            if not caps.get("tts"):
                return None
            asset = await self.host.synthesize(text[:MAX_TTS_CHARS], self.audio_dir)
        except Exception:  # TTS failure must not fail the turn
            log.warning("text to speech failed", exc_info=True)
            return None
        audio_id = self.register_audio(asset)
        return {"t": "speech", "url": self.audio_url(audio_id, base_path), "mime": asset.content_type}

    # ---- audio ---------------------------------------------------------

    def register_audio(self, asset: AssetFile) -> str:
        self._prune()
        audio_id = "a_" + secrets.token_hex(8)
        self._audio[audio_id] = (asset, time.time())
        return audio_id

    def audio_file(self, audio_id: str) -> AssetFile | None:
        self._prune()
        entry = self._audio.get(audio_id)
        return entry[0] if entry else None

    def audio_url(self, audio_id: str, base_path: str = "") -> str:
        return signed_url(self.store.signing_secret, f"{base_path.rstrip('/')}/api/v1/assets/audio/{audio_id}")

    # ---- actions -------------------------------------------------------

    async def stop(self, turn_id: str, *, device_id: str | None = None) -> None:
        turn = self.get(turn_id, device_id=device_id)
        if turn.done:
            return
        await self.host.stop_turn(turn.bot_id, turn.session_id)
        for _ in range(30):
            if turn.done or turn.finishing:
                break
            await asyncio.sleep(0.1)
        if not turn.done and not turn.finishing:
            if turn.task is not None:
                turn.task.cancel()
            await self._finish(turn, "cancelled", "")

    async def respond(
        self,
        turn_id: str,
        request_id: str,
        choice: str | None = None,
        answer: str | None = None,
        *,
        device_id: str | None = None,
    ) -> None:
        turn = self.get(turn_id, device_id=device_id)
        if turn.done:
            raise HostError("turn_finished", "This turn has already finished.", 409)
        if request_id not in turn.pending_asks:
            raise HostError("not_found", "No pending question with that id.", 404)
        if request_id in turn.responding_asks:
            raise HostError("ask_responding", "A response to this question is already in progress.", 409)
        # Claim synchronously before awaiting the upstream so concurrent requests
        # cannot both resolve a single approval.
        turn.responding_asks.add(request_id)
        try:
            await self.host.respond(turn.bot_id, turn.session_id, request_id, choice=choice, answer=answer)
        except BaseException:
            turn.responding_asks.discard(request_id)
            raise
        if self._resolve_ask(turn, request_id):
            self._append(turn, {"t": "ask_done", "request_id": request_id})

    def fleet_state(self, device_id: str) -> tuple[list[Json], list[Json]]:
        """Current prompts and working turns owned by one paired device."""
        self._prune()
        needs_you: list[Json] = []
        working: list[Json] = []
        for turn in self._turns.values():
            if turn.device_id != device_id or turn.done:
                continue
            if turn.pending_asks:
                needs_you.extend({"turn_id": turn.id, "bot_id": turn.bot_id, "ask": dict(ask)}
                                 for ask in turn.pending_asks.values())
            else:
                working.append({"turn_id": turn.id, "bot_id": turn.bot_id})
        return needs_you, working

    def finished(self, device_id: str) -> list[Json]:
        """Finished results belong to a paired device, even for shared bots."""
        return self.store.finished(device_id)

    # ---- overlay -------------------------------------------------------

    def overlay(self, bot_id: str) -> dict[str, Any]:
        """Bot freshness from this bridge's turns: state, active flag and last reply."""
        self._prune()
        state = "idle"
        for turn in self._turns.values():
            if turn.bot_id != bot_id or turn.done:
                continue
            if turn.pending_asks:
                state = "ask"
                break
            state = "busy"
        ended = self._bot_ended.get(bot_id)
        recent = ended is not None and time.time() - ended <= RECENT_END_WINDOW
        return {"state": state, "active": state != "idle" or recent, "last": self._last.get(bot_id)}

    # ---- shutdown ------------------------------------------------------

    async def close(self) -> None:
        tasks = [t.task for t in self._turns.values() if t.task is not None and not t.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
