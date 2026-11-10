"""MockHost: a scripted Host for demos, the Studio emulator and tests.

No Hermes needed. Special prompt words drive the script:
``approve`` (approval ask), ``clarify`` (3-choice question), ``fail`` (failed turn),
``long`` (about 1500 chars with lists and a code block). ``speed`` scales all delays;
tests use ``speed=0``. ``voice=True`` enables a fake transcription and a silent WAV.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from .host import AssetFile, HostError, Json, TurnHandle

APPROVAL_CHOICES = [
    {"id": "once", "label": "Allow once"},
    {"id": "session", "label": "Allow for session"},
    {"id": "always", "label": "Always allow"},
    {"id": "deny", "label": "Deny"},
]
CLARIFY_CHOICES = [
    {"id": "staging", "label": "Staging"},
    {"id": "production", "label": "Production"},
    {"id": "local", "label": "Local"},
]
TOOL_PREVIEWS = {
    "terminal": "git status --short",
    "read_file": "README.md",
    "web_search": "latest release notes",
}
LOCKED_REASON = "No API key for this profile. Run: hermes r1 setup"
CHUNK_CHARS = 16
CHUNK_DELAY = 0.04

BOTS: list[dict[str, Any]] = [
    {"id": "default", "name": "Hermes", "title": "Personal agent", "description": "Your main assistant.",
     "model": "claude-sonnet-4", "is_default": True, "reachable": True, "locked_reason": "",
     "avatar": {"kind": "shape", "shape": "circle", "color": "#8b5cf6"}},
    {"id": "coder", "name": "Coder", "title": "Writes and reviews code", "description": "Edits repos and runs tests.",
     "model": "claude-sonnet-4", "is_default": False, "reachable": True, "locked_reason": "",
     "avatar": {"kind": "shape", "shape": "squircle", "color": "hsl(210 68% 58%)"}},
    {"id": "researcher", "name": "Researcher", "title": "Finds and summarises sources",
     "description": "Searches the web and reads papers.", "model": "claude-opus-4", "is_default": False,
     "reachable": True, "locked_reason": "",
     "avatar": {"kind": "shape", "shape": "hexagon", "color": "hsl(150 55% 50%)"}},
    {"id": "writer", "name": "Writer", "title": "Drafts and edits prose", "description": "Emails, posts and docs.",
     "model": "claude-sonnet-4", "is_default": False, "reachable": True, "locked_reason": "",
     "avatar": {"kind": "shape", "shape": "drop", "color": "hsl(330 60% 62%)"}},
    {"id": "ops", "name": "Ops", "title": "Keeps things running", "description": "Checks services and logs.",
     "model": "claude-haiku-4", "is_default": False, "reachable": True, "locked_reason": "",
     "avatar": {"kind": "shape", "shape": "cloud", "color": "hsl(35 80% 56%)"}},
    {"id": "vault", "name": "Vault", "title": "Secrets keeper", "description": "Locked until a key is set.",
     "model": "", "is_default": False, "reachable": False, "locked_reason": LOCKED_REASON,
     "avatar": {"kind": "shape", "shape": "pill", "color": "hsl(260 30% 50%)"}},
]

LONG_FILLER = (
    "Each step stays small, so you can stop at any point, review the diff and resume "
    "without losing context. "
)


def _long_reply(prompt: str) -> str:
    lines = [
        f"Here is a longer walkthrough for: {prompt[:60]}.",
        "",
        "## Steps",
        "1. Inspect the current state of the workspace and note anything unusual.",
        "2. Read the relevant files and confirm the assumptions behind the change.",
        "3. Make the smallest edit that solves the problem, then run the checks.",
        "4. Summarise the result and list anything left for you to decide.",
        "",
        "## Key points",
        "- Device tokens are stored hashed and upstream keys never reach the device.",
        "- Long replies stream in small chunks so the screen can show them as they arrive.",
        "- Tool calls appear as one-line chips above the answer.",
        "",
        "```python",
        "def greet(name: str) -> str:",
        "    return f\"Hello, {name}. The mock bridge is alive.\"",
        "```",
        "",
    ]
    filler = [LONG_FILLER] * 8
    body = "\n".join(lines) + "\n" + "".join(filler)
    return body[:1500]


def _reply_for(prompt: str) -> str:
    short = " ".join(prompt.split())[:80]
    return (
        f'You asked: "{short}".\n\n'
        "Here is a quick status. The bridge is answering from mock data, the last three "
        "checks passed, and nothing needs your attention right now. Ask for a longer answer "
        "by including the word long."
    )


def _tools_for(prompt: str) -> list[str]:
    names = ["terminal", "read_file", "web_search"]
    start = sum(ord(c) for c in prompt) % len(names)
    return [names[start], names[(start + 1) % len(names)]]


def _chunks(text: str) -> list[str]:
    return [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)]


@dataclass
class _Run:
    session_id: str
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    request_id: str | None = None
    answer_event: asyncio.Event = field(default_factory=asyncio.Event)
    answer: dict[str, Any] = field(default_factory=dict)


class MockHost:
    """Scripted host implementing the ``Host`` protocol."""

    mock = True

    def __init__(self, speed: float = 1.0, voice: bool = False) -> None:
        self.speed = speed
        self.voice = voice
        self._bots = {b["id"]: b for b in BOTS}
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._messages: dict[str, list[dict[str, Any]]] = {}
        self._runs: dict[str, _Run] = {}
        self._seed()

    # ---- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        for run in list(self._runs.values()):
            run.cancel.set()

    # ---- seed data -----------------------------------------------------

    def _seed(self) -> None:
        now = time.time()
        seeds = {
            "default": [
                ("Morning standup", "What is on today?",
                 "Three things: the release notes, the docs review and one flaky test.", 3600),
                ("Weekend plans", "Any ideas for Saturday?",
                 "A short hike in the morning and a film in the evening would fit well.", 86400),
            ],
            "coder": [("Refactor parser", "Can you split the parser module?",
                       "Done. Three files now, and the tests still pass.", 7200)],
            "researcher": [("Vector DB survey", "Compare three vector databases.",
                            "Summary ready: all three handle our scale, pricing differs.", 172800)],
            "writer": [("Launch post", "Draft a short launch post.",
                        "Here is a draft with a friendly tone and three bullet points.", 259200)],
            "ops": [("Disk alert", "Why did the disk fill up?",
                     "Old build logs in one directory. I cleared them and set rotation.", 400000)],
        }
        for bot_id, items in seeds.items():
            self._sessions[bot_id] = []
            for title, user_text, assistant_text, age in items:
                sid = "s_" + secrets.token_hex(6)
                messages = [
                    self._message("user", user_text, now - age),
                    self._message("assistant", assistant_text, now - age + 5),
                ]
                self._messages[sid] = messages
                self._sessions[bot_id].append(self._session_row(sid, title, messages, now - age + 5))

    @staticmethod
    def _message(role: str, text: str, ts: float, tools: list[Json] | None = None) -> Json:
        return {"id": "m_" + secrets.token_hex(6), "role": role, "text": text, "ts": ts,
                "tools": tools or []}

    @staticmethod
    def _session_row(sid: str, title: str, messages: list[Json], last_active: float) -> Json:
        assistant = [m for m in messages if m["role"] == "assistant"]
        return {
            "id": sid,
            "title": title,
            "preview": (assistant[-1]["text"] if assistant else "")[:80],
            "last_active": last_active,
            "message_count": len(messages),
        }

    # ---- bots and assets -----------------------------------------------

    async def list_bots(self) -> list[Json]:
        out: list[Json] = []
        for raw in BOTS:
            bot = {k: (dict(v) if isinstance(v, dict) else v) for k, v in raw.items()}
            bot["avatar"] = {**raw["avatar"], "url": ""}
            bot["pet"] = None
            out.append(bot)
        return out

    async def avatar_file(self, bot_id: str) -> AssetFile | None:
        return None

    async def pet_file(self, bot_id: str) -> AssetFile | None:
        return None

    def _bot(self, bot_id: str) -> dict[str, Any]:
        bot = self._bots.get(bot_id)
        if bot is None:
            raise HostError("unknown_bot", f"No such bot: {bot_id}", 404)
        return bot

    # ---- sessions ------------------------------------------------------

    async def list_sessions(self, bot_id: str, limit: int) -> list[Json]:
        bot = self._bot(bot_id)
        if not bot["reachable"]:
            raise HostError("bot_locked", bot["locked_reason"], 409)
        rows = sorted(self._sessions.get(bot_id, []), key=lambda s: s["last_active"], reverse=True)
        return [dict(r) for r in rows[:limit]]

    async def get_messages(self, bot_id: str, session_id: str, limit: int) -> list[Json]:
        self._bot(bot_id)
        if session_id not in self._messages:
            raise HostError("not_found", "Unknown session.", 404)
        return [dict(m, tools=list(m["tools"])) for m in self._messages[session_id][-limit:]]

    # ---- turns ---------------------------------------------------------

    async def run_turn(self, bot_id: str, session_id: str | None, text: str) -> TurnHandle:
        bot = self._bot(bot_id)
        if not bot["reachable"]:
            raise HostError("bot_locked", bot["locked_reason"], 409)
        now = time.time()
        if session_id is None:
            session_id = "s_" + secrets.token_hex(6)
            title = " ".join(text.split())[:40] or "New chat"
            self._messages[session_id] = []
            self._sessions.setdefault(bot_id, []).insert(
                0, self._session_row(session_id, title, [], now))
        elif session_id not in self._messages:
            raise HostError("not_found", "Unknown session.", 404)
        self._messages[session_id].append(self._message("user", text, now))
        run = _Run(session_id=session_id)
        self._runs[session_id] = run
        return TurnHandle(session_id=session_id, events=self._script(run, bot_id, text))

    async def stop_turn(self, bot_id: str, session_id: str) -> None:
        run = self._runs.get(session_id)
        if run is not None:
            run.cancel.set()

    async def respond(self, bot_id: str, session_id: str, request_id: str,
                      *, choice: str | None = None, answer: str | None = None) -> None:
        run = self._runs.get(session_id)
        if run is None or run.request_id != request_id:
            raise HostError("not_found", "No pending question with that id.", 404)
        run.answer = {"choice": choice, "answer": answer}
        run.answer_event.set()

    # ---- voice ---------------------------------------------------------

    def voice_capabilities(self) -> Json:
        return {"stt": self.voice, "tts": self.voice}

    async def transcribe(self, audio_path: Path, content_type: str) -> str:
        if not self.voice:
            raise HostError("stt_unavailable", "Speech to text is not available.", 501)
        return "This is a mock transcription."

    async def synthesize(self, text: str, out_dir: Path) -> AssetFile:
        if not self.voice:
            raise HostError("tts_unavailable", "Text to speech is not available.", 501)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"mock_{secrets.token_hex(6)}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 4800)
        return AssetFile(path=path, content_type="audio/wav")

    # ---- scripted stream -----------------------------------------------

    async def _wait_any(self, events: list[asyncio.Event], timeout: float | None) -> None:
        tasks = [asyncio.ensure_future(e.wait()) for e in events]
        try:
            await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()

    async def _pause(self, run: _Run, seconds: float) -> None:
        delay = seconds * self.speed
        if run.cancel.is_set():
            return
        if delay <= 0:
            await asyncio.sleep(0)
            return
        await self._wait_any([run.cancel], delay)

    async def _script(self, run: _Run, bot_id: str, prompt: str) -> AsyncIterator[Json]:
        parts: list[str] = []
        tools: list[Json] = []
        result: dict[str, Any] = {"status": "completed", "error": ""}
        try:
            yield {"t": "start", "session_id": run.session_id}
            async for event in self._body(run, prompt, result):
                if event["t"] == "delta":
                    parts.append(event["text"])
                elif event["t"] == "tool" and event["phase"] == "end":
                    tools.append({"name": event["name"], "ok": event["ok"], "preview": event["preview"]})
                yield event
        except Exception as exc:  # the stream itself must still end cleanly
            result["status"] = "failed"
            result["error"] = str(exc) or type(exc).__name__
        finally:
            self._runs.pop(run.session_id, None)
        text = "".join(parts)
        if text:
            self._messages[run.session_id].append(self._message("assistant", text, time.time(), tools))
        self._refresh_session(bot_id, run.session_id)
        yield {
            "t": "end",
            "status": result["status"],
            "text": text,
            "error": result["error"],
            "usage": {"input": len(prompt) // 4 + 20, "output": len(text) // 4},
        }

    def _refresh_session(self, bot_id: str, session_id: str) -> None:
        messages = self._messages.get(session_id, [])
        for row in self._sessions.get(bot_id, []):
            if row["id"] == session_id:
                row.update(self._session_row(session_id, row["title"], messages, time.time()))

    async def _body(self, run: _Run, prompt: str, result: dict[str, Any]) -> AsyncIterator[Json]:
        lower = prompt.lower()
        if "clarify" in lower:
            body = self._clarify(run, prompt, result)
        elif "approve" in lower:
            body = self._approval(run, prompt, result)
        elif "fail" in lower:
            body = self._failure(run, prompt, result)
        elif "long" in lower:
            body = self._plain(run, _long_reply(prompt), result)
        else:
            body = self._default(run, prompt, result)
        async for event in body:
            yield event

    async def _stream_text(self, run: _Run, text: str, result: dict[str, Any]) -> AsyncIterator[Json]:
        for chunk in _chunks(text):
            if run.cancel.is_set():
                result["status"] = "cancelled"
                return
            yield {"t": "delta", "text": chunk}
            await self._pause(run, CHUNK_DELAY)

    async def _plain(self, run: _Run, text: str, result: dict[str, Any]) -> AsyncIterator[Json]:
        async for event in self._stream_text(run, text, result):
            yield event

    async def _tool(self, run: _Run, name: str, ok: bool, ms: int, call: str) -> AsyncIterator[Json]:
        preview = TOOL_PREVIEWS.get(name, name)
        yield {"t": "tool", "id": call, "name": name, "phase": "start", "preview": preview, "ok": True, "ms": 0}
        await self._pause(run, 0.4)
        yield {"t": "tool", "id": call, "name": name, "phase": "end", "preview": preview, "ok": ok, "ms": ms}

    async def _default(self, run: _Run, prompt: str, result: dict[str, Any]) -> AsyncIterator[Json]:
        yield {"t": "note", "text": "Checking what I need before answering."}
        for index, name in enumerate(_tools_for(prompt)):
            async for event in self._tool(run, name, True, 380, f"c_{index + 1}"):
                yield event
        async for event in self._stream_text(run, _reply_for(prompt), result):
            yield event

    async def _failure(self, run: _Run, prompt: str, result: dict[str, Any]) -> AsyncIterator[Json]:
        async for event in self._stream_text(run, "I started on that and", result):
            yield event
        if result["status"] == "completed":
            result["status"] = "failed"
            result["error"] = "Simulated failure: the upstream model timed out (mock)."

    async def _approval(self, run: _Run, prompt: str, result: dict[str, Any]) -> AsyncIterator[Json]:
        yield {"t": "note", "text": "This needs your approval before I run it."}
        yield {"t": "tool", "id": "c_approve", "name": "terminal", "phase": "start",
               "preview": "rm -rf build/", "ok": True, "ms": 0}
        request_id = "r_" + secrets.token_hex(6)
        run.request_id = request_id
        run.answer_event.clear()
        yield {"t": "ask", "request_id": request_id, "kind": "approval", "title": "Run a command?",
               "detail": "terminal: rm -rf build/", "choices": APPROVAL_CHOICES}
        await self._wait_any([run.cancel, run.answer_event], None)
        if run.cancel.is_set():
            result["status"] = "cancelled"
            return
        yield {"t": "ask_done", "request_id": request_id}
        choice = run.answer.get("choice") or "deny"
        if choice == "deny":
            yield {"t": "tool", "id": "c_approve", "name": "terminal", "phase": "end",
                   "preview": "blocked", "ok": False, "ms": 0}
            text = "Understood. The command was blocked, so I did not run it."
        else:
            label = next((c["label"] for c in APPROVAL_CHOICES if c["id"] == choice), choice)
            yield {"t": "tool", "id": "c_approve", "name": "terminal", "phase": "end",
                   "preview": "rm -rf build/", "ok": True, "ms": 900}
            text = f"Approved ({label}). The build directory is removed. Done."
        async for event in self._stream_text(run, text, result):
            yield event

    async def _clarify(self, run: _Run, prompt: str, result: dict[str, Any]) -> AsyncIterator[Json]:
        request_id = "r_" + secrets.token_hex(6)
        run.request_id = request_id
        run.answer_event.clear()
        yield {"t": "ask", "request_id": request_id, "kind": "clarify",
               "title": "Which environment?", "detail": "Several targets match. Pick one.",
               "choices": CLARIFY_CHOICES}
        await self._wait_any([run.cancel, run.answer_event], None)
        if run.cancel.is_set():
            result["status"] = "cancelled"
            return
        yield {"t": "ask_done", "request_id": request_id}
        choice = run.answer.get("choice")
        label = next((c["label"] for c in CLARIFY_CHOICES if c["id"] == choice), None)
        if label is None:
            label = run.answer.get("answer") or "the default target"
        text = f"Got it, using {label}. Continuing with: {' '.join(prompt.split())[:80]}."
        async for event in self._stream_text(run, text, result):
            yield event
