"""HermesHost: the real ``Host`` implementation, backed by a Hermes API server.

Verified against the Hermes source at ``/Users/tony/.hermes/hermes-agent``:

* Routes (``gateway/platforms/api_server.py`` ~L3970-3980, ``_http_route_table``
  ~L1557-1576): every ``/api/...`` and ``/v1/...`` route is also mounted under
  ``/p/<profile>/...`` when multiplexing is on.
* ``GET /api/sessions`` accepts ``source=`` (``_handle_list_sessions`` ~L2527) and
  returns ``{"object": "list", "data": [...]}`` with ``preview``/``last_active``.
* ``GET /api/sessions/{id}/messages`` returns ``{"data": [...]}`` of raw rows
  (``role`` user/assistant/tool/session_meta, ``tool_calls`` in OpenAI shape).
* ``POST /api/sessions`` returns ``{"object": "hermes.session", "session": {...}}``.
* ``POST /api/sessions/{id}/chat/stream`` takes ``{"input": text}`` (``_session_chat_user_message``
  ~L572). Events are SSE frames ``event: <name>`` + ``data: <json>``; the JSON carries
  ``run_id``/``session_id``/``seq``/``ts``. Keepalives are ``: keepalive`` comments
  (``CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS`` = 10). On an unexpected exception the stream
  emits an ``error`` event (not ``run.failed``) followed by ``done``.
* Approvals on the session stream (``gateway/platforms/api_server.py`` ``_approval_request_event``
  ~L90 and ``gateway/session_api_turn.py`` ``run_api_turn`` sink ~L320): ``approval.request``
  carries ``request_id`` (= prompt id), ``execution_generation`` (int), ``command``,
  ``description``, ``choices``, ``allow_session``/``allow_permanent``. ``approval.settled``
  carries ``prompt_id`` + ``execution_generation``.
* Clarify prompts (``gateway/session_pending_controls.py`` ``register_clarify`` ~L40) are
  published to the authority's viewer stream only, NOT to the session SSE queue. They are
  visible through ``GET /v1/runs/{run_id}`` as ``pending_controls`` entries
  (``gateway/platforms/api_server_authority_runs.py`` ``run_projection`` ~L68-96), with
  ``kind: "clarify"``, ``prompt_id``, ``execution_generation``, ``question``, ``choices``.
  Approval entries in ``pending_controls`` carry the same generation. So while the stream is
  quiet we poll that endpoint and emit ``ask`` / ``ask_done`` from it.
* Responses: ``POST /v1/runs/{id}/approval`` and ``/clarify`` on the authority path
  (``gateway/platforms/api_server_authority_runs.py`` ``respond_run`` ~L114) require the body
  to be EXACTLY ``{request_id, execution_generation: int, choice|answer}``; a stale generation
  is refused. ``POST /v1/runs/{id}/stop`` cancels the run.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import mimetypes
import re
import shutil
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

import aiohttp
import yaml

from .host import AssetFile, HostError, Json, TurnHandle

logger = logging.getLogger(__name__)

API_KEY_ENV = "API_SERVER_KEY"
LOCKED_REASON = "No API key for this profile. Run: hermes r1 setup"
AUTH_MESSAGE = "Hermes rejected the API key for this profile"
UNAVAILABLE_MESSAGE = "Hermes API server is not reachable. Is `hermes gateway` running?"

#: Seconds of stream silence before we poll ``pending_controls`` (module global so tests can shorten it).
_POLL_INTERVAL = 1.5
_POLL_TIMEOUT = aiohttp.ClientTimeout(total=5, sock_connect=3)
_FLEET_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=1.2, sock_connect=0.7)
_JSON_TIMEOUT = aiohttp.ClientTimeout(total=15, sock_connect=5)
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=120)
_BOTS_TTL = 5.0

_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_PET_GEOMETRY: dict[str, Any] = {"cols": 8, "rows": 9, "cw": 192, "ch": 208, "frames": 6, "loop_ms": 1100}
_CHOICE_LABELS = {"once": "Allow once", "session": "Allow for this session", "always": "Always allow", "deny": "Deny"}
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_AUDIO_TYPES = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


# --------------------------------------------------------------------------- .env handling


def _parse_env_text(text: str) -> dict[str, str]:
    """Parse dotenv-style text. Later assignments win. Values are never logged anywhere."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not _ENV_NAME_RE.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            comment = re.search(r"\s#", value)
            if comment:
                value = value[: comment.start()].rstrip()
        values[name] = value
    return values


def read_env_value(path: Path, key: str) -> str | None:
    """Value of ``key`` in a ``.env`` file (last assignment wins), or None when absent or empty."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return _parse_env_text(text).get(key) or None


class _EnvCache:
    """Caches parsed ``.env`` files; re-reads a file when its mtime or size changes."""

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[int, int, dict[str, str]]] = {}

    def get(self, path: Path, key: str) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            self._entries.pop(path, None)
            return None
        cached = self._entries.get(path)
        if cached is None or cached[0] != stat.st_mtime_ns or cached[1] != stat.st_size:
            try:
                values = _parse_env_text(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                values = {}
            cached = (stat.st_mtime_ns, stat.st_size, values)
            self._entries[path] = cached
        return cached[2].get(key) or None


# --------------------------------------------------------------------------- small helpers


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_safe_segment(name: Any) -> bool:
    return isinstance(name, str) and name not in (".", "..") and _ID_RE.fullmatch(name) is not None


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _has_profile_marker(home: Path) -> bool:
    return any((home / name).is_file() for name in ("config.yaml", ".env", "SOUL.md", "profile.yaml"))


def _avatar_path(home: Path) -> Path | None:
    for suffix in (".png", ".jpg", ".webp"):
        candidate = home / "assets" / f"avatar{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _pet_sheet(home: Path, config: dict[str, Any]) -> Path | None:
    """The spritesheet of the enabled pet (``display.pet``), or None."""
    display = config.get("display")
    pet = display.get("pet") if isinstance(display, dict) else None
    if not isinstance(pet, dict) or pet.get("enabled") is not True:
        return None
    slug = _text(pet.get("slug"))
    if not _is_safe_segment(slug):
        return None
    pet_dir = home / "pets" / slug
    if not pet_dir.is_dir():
        return None
    declared = _text(_read_json_object(pet_dir / "pet.json").get("spritesheetPath"))
    names = ([declared] if declared else []) + ["spritesheet.webp", "spritesheet.png", "sprite.webp", "sprite.png"]
    root = pet_dir.resolve()
    for name in names:
        candidate = pet_dir / name
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_TYPES:
        return _IMAGE_TYPES[suffix]
    if suffix in _AUDIO_TYPES:
        return _AUDIO_TYPES[suffix]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _status_error(status: int, not_found: str) -> HostError | None:
    if status < 200 or status >= 300:
        if status < 300:
            return HostError("upstream_error", "Hermes API server returned an unexpected response.", 502)
    if 300 <= status < 400:
        return HostError("upstream_error", "Hermes API server returned an unexpected redirect.", 502)
    if status < 400:
        return None
    if status in (401, 403):
        return HostError("upstream_auth", AUTH_MESSAGE, 502)
    if status == 404:
        return HostError("not_found", not_found, 404)
    if status == 409:
        return HostError(
            "session_owned_elsewhere",
            "That session belongs to another Hermes surface and cannot be used from the R1.",
            409,
        )
    if status == 429:
        return HostError("busy", "Hermes is busy. Try again in a moment.", 429)
    return HostError("upstream_error", f"Hermes API server returned HTTP {status}.", 502)


def _checked_session_id(session_id: Any) -> str:
    if (
        not isinstance(session_id, str)
        or not session_id
        or len(session_id) > 200
        or any(ch in session_id for ch in "/\\\x00") or session_id != session_id.strip()
    ):
        raise HostError("not_found", "Unknown session.", 404)
    return session_id


def _loads_object(data: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _content_text(content: Any) -> str:
    """Text of a Hermes message ``content``: a string, or a list of parts (text parts joined)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"):
                value = part.get("text")
                if isinstance(value, str):
                    pieces.append(value)
        return "\n".join(pieces)
    return ""


def _args_preview(args: Any) -> str:
    """Short one-line summary of tool arguments: the first string value of a JSON object."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            text = " ".join(args.split())
            return text[:80]
    if isinstance(args, dict):
        for value in args.values():
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:80]
    return ""


def _result_ok(content: Any) -> bool:
    """A tool result is a failure when its JSON has a truthy ``error`` or a non-zero ``exit_code``."""
    text = _content_text(content) if not isinstance(content, str) else content
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return True
    if not isinstance(value, dict):
        return True
    if value.get("error"):
        return False
    code = value.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool) and code != 0:
        return False
    return True


def _tool_calls(row: dict[str, Any]) -> list[dict[str, Any]]:
    calls = row.get("tool_calls")
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except ValueError:
            return []
    if not isinstance(calls, list):
        return []
    return [call for call in calls if isinstance(call, dict)]


def _map_session(row: dict[str, Any]) -> Json:
    preview = " ".join(_text(row.get("preview")).split())[:200]
    title = _text(row.get("title")) or preview or "New chat"
    last = row.get("last_active") or row.get("started_at") or 0
    try:
        last_active = float(last)
    except (TypeError, ValueError):
        last_active = 0.0
    try:
        message_count = int(row.get("message_count") or 0)
    except (TypeError, ValueError):
        message_count = 0
    return {
        "id": str(row.get("id") or ""),
        "title": title,
        "preview": preview,
        "last_active": last_active,
        "message_count": message_count,
    }


def _fold_messages(rows: list[Any]) -> list[Json]:
    """Fold raw Hermes rows into SPEC Messages.

    user rows become one message each (empty ones skipped). All assistant rows between two user
    rows form ONE assistant message: their non-empty texts are joined with a blank line and every
    tool call (including tool-only assistant rows) is listed in ``tools``. ``tool`` rows are only
    used to decide ``ok`` for the matching call. Other roles (session_meta, system) are ignored.
    """
    dict_rows = [row for row in rows if isinstance(row, dict)]
    results: dict[str, dict[str, Any]] = {}
    for row in dict_rows:
        if row.get("role") == "tool":
            call_id = row.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                results[call_id] = row

    out: list[Json] = []
    group: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal group
        if group is not None and (group["texts"] or group["tools"]):
            out.append({
                "id": group["id"], "role": "assistant", "text": "\n\n".join(group["texts"]),
                "ts": group["ts"], "tools": group["tools"],
            })
        group = None

    for index, row in enumerate(dict_rows):
        role = row.get("role")
        row_id = str(row.get("id") or f"row{index}")
        try:
            ts = float(row.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if role == "user":
            flush()
            text = _content_text(row.get("content")).strip()
            if text:
                out.append({"id": row_id, "role": "user", "text": text, "ts": ts, "tools": []})
        elif role == "assistant":
            if group is None:
                group = {"id": row_id, "ts": ts, "texts": [], "tools": []}
            text = _content_text(row.get("content")).strip()
            if text:
                group["texts"].append(text)
            for call in _tool_calls(row):
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = str(fn.get("name") or call.get("name") or "")
                call_id = call.get("id") or call.get("call_id")
                result = results.get(call_id) if isinstance(call_id, str) else None
                group["tools"].append({
                    "name": name,
                    "ok": _result_ok(result.get("content")) if result is not None else True,
                    "preview": _args_preview(fn.get("arguments", call.get("arguments"))),
                })
    flush()
    return out


def _ask_event(request_id: str, kind: str, payload: dict[str, Any]) -> Json:
    if kind == "approval":
        raw_choices = payload.get("choices") or ["once", "deny"]
        choices = [
            {"id": str(choice), "label": _CHOICE_LABELS.get(str(choice), str(choice))}
            for choice in raw_choices if isinstance(choice, str)
        ]
        detail = "\n\n".join(part for part in (_text(payload.get("description")), _text(payload.get("command"))) if part)
        return {"t": "ask", "request_id": request_id, "kind": "approval", "title": "Approval needed",
                "detail": detail[:600], "choices": choices}
    raw_choices = payload.get("choices") or []
    choices = [{"id": choice, "label": choice} for choice in raw_choices if isinstance(choice, str) and choice]
    return {"t": "ask", "request_id": request_id, "kind": "clarify", "title": "Question",
            "detail": _text(payload.get("question"))[:600], "choices": choices}


async def _iter_sse_lines(chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
    buffer = b""
    async for chunk in chunks:
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            yield raw.rstrip(b"\r").decode("utf-8", "replace")
    if buffer:
        yield buffer.rstrip(b"\r").decode("utf-8", "replace")


async def iter_sse(chunks: AsyncIterator[bytes]) -> AsyncIterator[tuple[str | None, str]]:
    """Parse an SSE byte stream into ``(event, data)`` pairs. Handles CRLF, multi-line data,
    ``:`` comment keepalives and events split across chunks. Frames without data are dropped."""
    event: str | None = None
    data: list[str] = []
    async for line in _iter_sse_lines(chunks):
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        field_name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field_name == "event":
            event = value
        elif field_name == "data":
            data.append(value)
    if data:
        yield event, "\n".join(data)


async def _pump_sse(resp: aiohttp.ClientResponse, queue: asyncio.Queue[tuple]) -> None:
    try:
        async for name, data in iter_sse(resp.content.iter_any()):
            queue.put_nowait(("sse", name, data))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # network drop, payload error, timeout
        queue.put_nowait(("error", exc))
        return
    queue.put_nowait(("eof",))


class _Turn:
    """Per-turn state: maps upstream stream events to SPEC TurnEvents and tracks asks."""

    def __init__(self, bot_id: str, sid: str) -> None:
        self.bot_id = bot_id
        self.sid = sid
        self.run_id: str | None = None
        self.finished = False
        self.parts: list[str] = []
        self.final: str | None = None
        self.usage_in = 0
        self.usage_out = 0
        self.tool_seq = 0
        self.open_tools: dict[str, deque[tuple[str, str, float | None]]] = {}
        self.gens: dict[str, int] = {}
        self.asks: dict[str, str] = {}

    # -- bookkeeping

    def _remember_generation(self, request_id: str, generation: Any) -> None:
        if isinstance(generation, int) and not isinstance(generation, bool):
            self.gens[request_id] = generation

    def _read_usage(self, payload: dict[str, Any]) -> None:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
        if isinstance(tokens_in, int) and not isinstance(tokens_in, bool):
            self.usage_in = tokens_in
        if isinstance(tokens_out, int) and not isinstance(tokens_out, bool):
            self.usage_out = tokens_out

    def finish(self, status: str, payload: dict[str, Any] | None = None, error: str = "") -> Json:
        if payload:
            self._read_usage(payload)
        self.finished = True
        text = self.final if self.final is not None else "".join(self.parts)
        return {"t": "end", "status": status, "text": text, "error": error,
                "usage": {"input": self.usage_in, "output": self.usage_out}}

    # -- upstream stream events

    def on_sse(self, name: str | None, data: str) -> list[Json]:
        payload = _loads_object(data)
        if name == "run.started":
            run_id = payload.get("run_id")
            if isinstance(run_id, str) and run_id:
                self.run_id = run_id
            return [{"t": "start", "session_id": self.sid}]
        if name == "assistant.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                self.parts.append(delta)
                return [{"t": "delta", "text": delta}]
            return []
        if name == "assistant.commentary":
            text = _text(payload.get("text"))
            if payload.get("already_streamed") or not text:
                return []
            return [{"t": "note", "text": text}]
        if name == "assistant.completed":
            content = payload.get("content")
            if isinstance(content, str):
                self.final = content
            return []
        if name == "tool.started":
            tool_name = str(payload.get("tool_name") or "")
            preview = _text(payload.get("preview"))
            self.tool_seq += 1
            tool_id = f"tool_{self.tool_seq}"
            self.open_tools.setdefault(tool_name, deque()).append((tool_id, preview, _timestamp(payload)))
            return [{"t": "tool", "id": tool_id, "name": tool_name, "phase": "start",
                     "preview": preview, "ok": True, "ms": 0}]
        if name in ("tool.completed", "tool.failed"):
            tool_name = str(payload.get("tool_name") or "")
            queue = self.open_tools.get(tool_name)
            started: tuple[str, str, float | None]
            if queue:
                started = queue.popleft()
                if not queue:
                    del self.open_tools[tool_name]
            else:
                self.tool_seq += 1
                started = (f"tool_{self.tool_seq}", _text(payload.get("preview")), None)
            tool_id, preview, started_at = started
            ended_at = _timestamp(payload)
            ms = 0
            if started_at is not None and ended_at is not None:
                ms = int(max(0.0, ended_at - started_at) * 1000)
            return [{"t": "tool", "id": tool_id, "name": tool_name, "phase": "end", "preview": preview,
                     "ok": name == "tool.completed", "ms": ms}]
        if name in ("approval.request", "clarify.request"):
            kind = "approval" if name == "approval.request" else "clarify"
            request_id = _text(payload.get("request_id") or payload.get("prompt_id"))
            if not request_id or request_id in self.asks:
                return []
            self.asks[request_id] = kind
            self._remember_generation(request_id, payload.get("execution_generation"))
            return [_ask_event(request_id, kind, payload)]
        if name in ("approval.settled", "clarify.settled"):
            request_id = _text(payload.get("prompt_id") or payload.get("request_id"))
            if request_id and request_id in self.asks:
                del self.asks[request_id]
                self.gens.pop(request_id, None)
                return [{"t": "ask_done", "request_id": request_id}]
            return []
        if name == "error":
            message = _text(payload.get("message")) or "Hermes reported an error."
            return [self.finish("failed", payload, message)]
        if name == "run.completed":
            return [self.finish("completed", payload)]
        if name == "run.failed":
            message = _text(payload.get("error") or payload.get("message")) or "The run failed."
            return [self.finish("failed", payload, message)]
        if name in ("run.cancelled", "run.interrupted"):
            return [self.finish("cancelled", payload)]
        return []

    def poll_events(self, prompts: list[dict[str, Any]]) -> list[Json]:
        """Reconcile asks with a ``pending_controls`` snapshot from ``GET /v1/runs/{id}``."""
        events: list[Json] = []
        seen: set[str] = set()
        for prompt in prompts:
            kind = prompt.get("kind")
            request_id = _text(prompt.get("prompt_id"))
            if kind not in ("approval", "clarify") or not request_id:
                continue
            seen.add(request_id)
            self._remember_generation(request_id, prompt.get("execution_generation"))
            if request_id not in self.asks:
                self.asks[request_id] = kind
                events.append(_ask_event(request_id, kind, prompt))
        for request_id in [rid for rid in self.asks if rid not in seen]:
            del self.asks[request_id]
            self.gens.pop(request_id, None)
            events.append({"t": "ask_done", "request_id": request_id})
        return events


def _timestamp(payload: dict[str, Any]) -> float | None:
    value = payload.get("ts")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# --------------------------------------------------------------------------- the host


class HermesHost:
    """Talks to a local Hermes API server and reads profiles from ``HERMES_HOME``."""

    mock = False

    def __init__(self, hermes_home: Path, api_url: str = "http://127.0.0.1:8642") -> None:
        self._home = Path(hermes_home).expanduser()
        self._api_url = api_url.rstrip("/")
        self._env = _EnvCache()
        self._session: aiohttp.ClientSession | None = None
        self._bots_cache: tuple[float, list[Json]] | None = None
        self._runs: dict[tuple[str, str], _Turn] = {}
        self._stt: Any = None
        self._tts: Any = None
        self._voice_probed = False

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._client()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def fleet_probe(self) -> str:
        """Return only transport reachability for the configured Hermes gateway."""
        try:
            async with self._client().get(f"{self._api_url}/health", timeout=_FLEET_PROBE_TIMEOUT,
                                          allow_redirects=False) as response:
                return "online" if 200 <= response.status < 300 else "offline"
        except asyncio.CancelledError:
            raise
        except Exception:
            return "offline"

    async def fleet_gateways(self) -> list[Json]:
        """Safe Fleet Now gateway projection for a single local Hermes host."""
        return [{"id": "local", "name": "Local", "status": await self.fleet_probe()}]

    # ------------------------------------------------------------------ profiles

    def _profile_home(self, bot_id: str) -> tuple[Path, bool]:
        """Resolve a bot id to its profile directory. Rejects anything that is not a bare name."""
        if not _is_safe_segment(bot_id):
            raise HostError("not_found", "Unknown bot.", 404)
        if bot_id == "default":
            return self._home, True
        home = self._home / "profiles" / bot_id
        if not home.is_dir() or not _has_profile_marker(home):
            raise HostError("not_found", "Unknown bot.", 404)
        return home, False

    def _api_key(self, home: Path) -> str | None:
        return self._env.get(home / ".env", API_KEY_ENV)

    def _upstream(self, bot_id: str) -> tuple[str, str]:
        """``(base_url, api_key)`` for a bot. Raises ``bot_locked`` when it has no key."""
        home, is_default = self._profile_home(bot_id)
        key = self._api_key(home)
        if not key:
            raise HostError("bot_locked", LOCKED_REASON, 409)
        base = self._api_url if is_default else f"{self._api_url}/p/{bot_id}"
        return base, key

    def _build_bot(self, bot_id: str, home: Path, is_default: bool) -> Json:
        profile = _read_yaml(home / "profile.yaml")
        config = _read_yaml(home / "config.yaml")
        meta = profile.get("ui_meta")
        hermes_bots = meta.get("hermes-bots") if isinstance(meta, dict) else None
        hermes_bots = hermes_bots if isinstance(hermes_bots, dict) else {}

        display_name = _text(profile.get("display_name"))
        if display_name:
            name = display_name
        elif is_default:
            name = "Hermes"
        else:
            name = bot_id[:1].upper() + bot_id[1:]

        description = " ".join(_text(profile.get("description")).split())[:300]
        model_cfg = config.get("model")
        if isinstance(model_cfg, dict):
            model = _text(model_cfg.get("default"))
        elif isinstance(model_cfg, str):
            model = model_cfg.strip()
        else:
            model = ""

        reachable = bool(self._api_key(home))
        is_photo = hermes_bots.get("imageKind") == "photo" and _avatar_path(home) is not None
        pet = dict(_PET_GEOMETRY, url="") if _pet_sheet(home, config) is not None else None
        return {
            "id": bot_id,
            "name": name,
            "title": _text(hermes_bots.get("title")),
            "description": description,
            "model": model,
            "is_default": is_default,
            "reachable": reachable,
            "locked_reason": "" if reachable else LOCKED_REASON,
            "avatar": {
                "kind": "image" if is_photo else "shape",
                "shape": _text(hermes_bots.get("shape")),
                "color": _text(hermes_bots.get("color")),
                "url": "",
            },
            "pet": pet,
            "state": "idle",
            "active": False,
            "last": None,
        }

    def _discover(self) -> list[Json]:
        bots = [self._build_bot("default", self._home, True)]
        root = self._home / "profiles"
        try:
            children = sorted(root.iterdir()) if root.is_dir() else []
        except OSError:
            children = []
        for child in children:
            name = child.name
            if name == "default" or not _is_safe_segment(name) or not child.is_dir():
                continue
            if not _has_profile_marker(child):
                continue
            bots.append(self._build_bot(name, child, False))
        return bots

    async def list_bots(self) -> list[Json]:
        now = time.monotonic()
        if self._bots_cache is None or now - self._bots_cache[0] >= _BOTS_TTL:
            self._bots_cache = (now, self._discover())
        return copy.deepcopy(self._bots_cache[1])

    async def avatar_file(self, bot_id: str) -> AssetFile | None:
        try:
            home, _ = self._profile_home(bot_id)
        except HostError:
            return None
        path = _avatar_path(home)
        return AssetFile(path, _content_type(path)) if path is not None else None

    async def pet_file(self, bot_id: str) -> AssetFile | None:
        try:
            home, _ = self._profile_home(bot_id)
        except HostError:
            return None
        path = _pet_sheet(home, _read_yaml(home / "config.yaml"))
        return AssetFile(path, _content_type(path)) if path is not None else None

    # ------------------------------------------------------------------ HTTP helpers

    async def _json_request(
        self, method: str, url: str, key: str, *, body: Any = None,
        not_found: str = "Not found.", timeout: aiohttp.ClientTimeout = _JSON_TIMEOUT,
    ) -> Any:
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        try:
            async with self._client().request(
                method, url, json=body, headers=headers, timeout=timeout, allow_redirects=False,
            ) as resp:
                error = _status_error(resp.status, not_found)
                if error is not None:
                    if resp.status == 400:
                        try:
                            payload = await resp.json(content_type=None)
                        except (ValueError, aiohttp.ClientError):
                            payload = None
                        upstream_error = payload.get("error") if isinstance(payload, dict) else None
                        if isinstance(upstream_error, dict) and upstream_error.get("code") == "invalid_title":
                            raise HostError("invalid_title", "Hermes rejected the session title.", 400)
                    raise error
                try:
                    return await resp.json(content_type=None)
                except (ValueError, aiohttp.ClientError) as exc:
                    raise HostError("upstream_error", "Hermes returned an unreadable response.", 502) from exc
        except HostError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise HostError("upstream_unavailable", UNAVAILABLE_MESSAGE, 502) from exc

    async def _open_stream(self, url: str, key: str, body: Json) -> aiohttp.ClientResponse:
        headers = {"Authorization": f"Bearer {key}", "Accept": "text/event-stream"}
        try:
            resp = await self._client().post(url, json=body, headers=headers, timeout=_STREAM_TIMEOUT,
                                             allow_redirects=False)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise HostError("upstream_unavailable", UNAVAILABLE_MESSAGE, 502) from exc
        if not 200 <= resp.status < 300:
            resp.close()
            raise _status_error(resp.status, "Session not found.") or HostError(
                "upstream_error", "Hermes API server refused the turn.", 502)
        return resp

    async def _poll_prompts(self, base: str, key: str, run_id: str) -> list[Json] | None:
        """``pending_controls`` of a run, or None when the run cannot be read right now."""
        url = f"{base}/v1/runs/{quote(run_id, safe='')}"
        try:
            data = await self._json_request("GET", url, key, not_found="Run not found.", timeout=_POLL_TIMEOUT)
        except HostError as exc:
            logger.debug("run poll failed: %s", exc.code)
            return None
        if not isinstance(data, dict) or not isinstance(data.get("pending_controls"), list):
            return None
        return [item for item in data["pending_controls"] if isinstance(item, dict)]

    # ------------------------------------------------------------------ sessions

    async def list_sessions(self, bot_id: str, limit: int) -> list[Json]:
        base, key = self._upstream(bot_id)
        wanted = max(1, min(int(limit), 100))
        fetch = min(wanted * 3, 100)
        data = await self._json_request(
            "GET", f"{base}/api/sessions?limit={fetch}&source=api_server", key)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = []
        sessions = [
            _map_session(row) for row in rows
            if isinstance(row, dict) and row.get("source") == "api_server"
        ]
        sessions.sort(key=lambda session: session["last_active"], reverse=True)
        return sessions[:wanted]

    async def get_messages(self, bot_id: str, session_id: str, limit: int) -> list[Json]:
        base, key = self._upstream(bot_id)
        sid = _checked_session_id(session_id)
        data = await self._json_request(
            "GET", f"{base}/api/sessions/{quote(sid, safe='')}/messages?inline_images=false",
            key, not_found="Session not found.")
        rows = data.get("data") if isinstance(data, dict) else None
        messages = _fold_messages(rows if isinstance(rows, list) else [])
        return messages[-max(1, int(limit)):]

    async def _create_session(self, base: str, key: str, text: str) -> str:
        url = f"{base}/api/sessions"
        try:
            data = await self._json_request("POST", url, key, body={"title": text.strip()[:40]})
        except HostError as exc:
            if exc.code != "invalid_title":
                raise
            # Hermes rejects duplicate titles before committing the new session.
            data = await self._json_request("POST", url, key, body={})
        session = data.get("session") if isinstance(data, dict) else None
        if not isinstance(session, dict):
            session = data if isinstance(data, dict) else {}
        sid = session.get("id")
        if not isinstance(sid, str) or not sid:
            raise HostError("upstream_error", "Hermes did not return a session id.", 502)
        return sid

    # ------------------------------------------------------------------ turns

    async def run_turn(self, bot_id: str, session_id: str | None, text: str) -> TurnHandle:
        base, key = self._upstream(bot_id)
        sid = await self._create_session(base, key, text) if session_id is None else _checked_session_id(session_id)
        resp = await self._open_stream(
            f"{base}/api/sessions/{quote(sid, safe='')}/chat/stream", key, {"input": text})
        turn = _Turn(bot_id, sid)
        self._runs[(bot_id, sid)] = turn
        return TurnHandle(session_id=sid, events=self._stream_events(base, key, turn, resp))

    async def _stream_events(
        self, base: str, key: str, turn: _Turn, resp: aiohttp.ClientResponse,
    ) -> AsyncIterator[Json]:
        queue: asyncio.Queue[tuple] = asyncio.Queue()
        reader = asyncio.ensure_future(_pump_sse(resp, queue))
        try:
            while not turn.finished:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_POLL_INTERVAL)
                except asyncio.TimeoutError:
                    # The stream is quiet: approvals/clarify prompts may be waiting on the user.
                    if turn.run_id:
                        prompts = await self._poll_prompts(base, key, turn.run_id)
                        if prompts is not None:
                            for event in turn.poll_events(prompts):
                                yield event
                    continue
                if item[0] == "sse":
                    events = turn.on_sse(item[1], item[2])
                elif item[0] == "error":
                    events = [turn.finish("failed", error=f"Lost the connection to Hermes: {item[1]}")]
                else:
                    events = [turn.finish("failed", error="Hermes closed the stream before the run finished.")]
                for event in events:
                    yield event
                    if event["t"] == "end":
                        break
        finally:
            reader.cancel()
            resp.close()
            if self._runs.get((turn.bot_id, turn.sid)) is turn:
                del self._runs[(turn.bot_id, turn.sid)]

    async def stop_turn(self, bot_id: str, session_id: str) -> None:
        turn = self._runs.get((bot_id, session_id))
        if turn is None or not turn.run_id:
            return
        base, key = self._upstream(bot_id)
        try:
            await self._json_request(
                "POST", f"{base}/v1/runs/{quote(turn.run_id, safe='')}/stop", key, body={},
                not_found="That run has already finished.")
        except HostError as exc:
            if exc.code != "not_found":
                raise

    async def respond(self, bot_id: str, session_id: str, request_id: str,
                      *, choice: str | None = None, answer: str | None = None) -> None:
        base, key = self._upstream(bot_id)
        turn = self._runs.get((bot_id, session_id))
        if turn is None or not turn.run_id:
            raise HostError("ask_gone", "That question is no longer pending.", 409)
        body: Json = {"request_id": request_id, "execution_generation": turn.gens.get(request_id)}
        if choice is not None:
            path, body["choice"] = "approval", choice
        elif answer is not None:
            path, body["answer"] = "clarify", answer
        else:
            raise HostError("bad_request", "Give a choice or an answer.", 400)
        url = f"{base}/v1/runs/{quote(turn.run_id, safe='')}/{path}"
        try:
            await self._json_request("POST", url, key, body=body, not_found="That question is no longer pending.")
        except HostError as exc:
            if exc.status in (404, 409):
                raise HostError("ask_gone", "That question was already answered or expired.", 409) from exc
            raise

    # ------------------------------------------------------------------ voice

    def _probe_voice(self) -> None:
        if self._voice_probed:
            return
        self._voice_probed = True
        try:
            from tools.transcription_tools import transcribe_audio  # type: ignore[import-not-found]
            self._stt = transcribe_audio
        except Exception as exc:  # not running inside the Hermes environment
            logger.info("speech-to-text unavailable: %s", type(exc).__name__)
        try:
            from tools.tts_tool import text_to_speech_tool  # type: ignore[import-not-found]
            self._tts = text_to_speech_tool
        except Exception as exc:
            logger.info("text-to-speech unavailable: %s", type(exc).__name__)

    def voice_capabilities(self) -> Json:
        self._probe_voice()
        return {"stt": self._stt is not None, "tts": self._tts is not None}

    async def transcribe(self, audio_path: Path, content_type: str) -> str:
        self._probe_voice()
        if self._stt is None:
            raise HostError("stt_unavailable", "Speech-to-text is not available in this Hermes install.", 501)
        try:
            result = await asyncio.to_thread(self._stt, str(audio_path))
        except Exception as exc:
            raise HostError("stt_failed", f"Transcription failed ({type(exc).__name__}).", 502) from exc
        if not isinstance(result, dict) or not result.get("success"):
            detail = _text(result.get("error")) if isinstance(result, dict) else ""
            raise HostError("stt_failed", f"Transcription failed. {detail[:200]}".strip(), 502)
        return str(result.get("transcript") or "").strip()

    async def synthesize(self, text: str, out_dir: Path) -> AssetFile:
        self._probe_voice()
        if self._tts is None:
            raise HostError("tts_unavailable", "Text-to-speech is not available in this Hermes install.", 501)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            raw = await asyncio.to_thread(self._tts, text=text)
        except Exception as exc:
            raise HostError("tts_failed", f"Speech synthesis failed ({type(exc).__name__}).", 502) from exc
        data = _loads_object(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
        if not data.get("success"):
            raise HostError("tts_failed", "Speech synthesis failed.", 502)
        produced = data.get("file_paths") or [data.get("file_path")]
        produced = [Path(p) for p in produced if isinstance(p, str) and p]
        if not produced:
            raise HostError("tts_failed", "Speech synthesis produced no file.", 502)
        moved: list[Path] = []
        for source in produced:
            target = out_dir / f"speech_{uuid.uuid4().hex}{source.suffix.lower()}"
            try:
                shutil.move(str(source), str(target))
            except OSError:
                shutil.copyfile(source, target)
            moved.append(target)
        return AssetFile(moved[0], _content_type(moved[0]))
