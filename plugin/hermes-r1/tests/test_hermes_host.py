"""HermesHost against a fake Hermes API server that replays the real captures in docs/fixtures."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from conftest import FIXTURES
from hermes_r1.bridge import hermes_host
from hermes_r1.bridge.host import HostError
from hermes_r1.bridge.hermes_host import HermesHost, _fold_messages, _result_ok, iter_sse, read_env_value

ROOT_KEY = "root-test-key"
WORK_KEY = "work-key"
SESSION_ID = "api_1794267717_316de638"
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da6364f8cf"
    "c0f01f0005000201a5f6b1f00000000049454e44ae426082"
)
FIXTURE_SSE = (FIXTURES / "chat_stream.sse").read_bytes()
FIXTURE_MESSAGES = json.loads((FIXTURES / "messages.json").read_text())
FIXTURE_SESSION = json.loads((FIXTURES / "session_create.json").read_text())


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hermes_host, "_POLL_INTERVAL", 0.05)


# --------------------------------------------------------------------------- fake Hermes


class FakeHermes:
    """Minimal stand-in for the Hermes API server (default profile + /p/<profile> mirrors)."""

    def __init__(self) -> None:
        self.keys = {"default": ROOT_KEY, "work": WORK_KEY}
        self.requests: list[dict[str, Any]] = []
        self.created_titles: list[str] = []
        self.chat_inputs: list[str] = []
        self.mode = "fixture"
        self.gate = asyncio.Event()
        self.stopped = False
        self.bodies: list[tuple[str, dict[str, Any]]] = []

    def pending_prompts(self) -> list[dict[str, Any]]:
        if self.gate.is_set():
            return []
        if self.mode == "approval":
            return [{"kind": "approval", "prompt_id": "ap_1", "execution_generation": 7,
                     "command": "rm -rf /tmp/x", "description": "Delete temp", "choices": ["once", "deny"]}]
        if self.mode == "clarify":
            return [{"kind": "clarify", "prompt_id": "cl_1", "execution_generation": 3,
                     "question": "Which one?", "choices": ["alpha", "beta"], "multi_select": False}]
        return []

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.dispatch)
        return app

    async def dispatch(self, request: web.Request) -> web.StreamResponse:
        path, profile = request.path, "default"
        match = re.match(r"^/p/([^/]+)(/.*)$", path)
        if match:
            profile, path = match.group(1), match.group(2)
        body: dict[str, Any] = {}
        if request.can_read_body and request.content_type == "application/json":
            body = await request.json()
        self.requests.append({"method": request.method, "path": path, "profile": profile,
                              "query": dict(request.query), "body": body})
        if self.keys.get(profile) is None or request.headers.get("Authorization") != f"Bearer {self.keys[profile]}":
            return web.json_response({"error": {"message": "bad key"}}, status=401)

        if request.method == "GET" and path == "/api/sessions":
            return web.json_response({"object": "list", "data": [
                {"id": "api_s1", "source": "api_server", "title": "", "preview": "Hello there",
                 "last_active": 200.0, "started_at": 100.0, "message_count": 3},
                {"id": "cli_1", "source": "cli", "title": "CLI chat", "preview": "", "last_active": 300.0,
                 "started_at": 50.0, "message_count": 9},
                {"id": "api_s2", "source": "api_server", "title": "Newest", "preview": "", "last_active": 400.0,
                 "started_at": 10.0, "message_count": 1},
            ]})
        if request.method == "POST" and path == "/api/sessions":
            self.created_titles.append(body.get("title"))
            return web.json_response(FIXTURE_SESSION)
        if request.method == "GET" and re.fullmatch(r"/api/sessions/[^/]+/messages", path):
            return web.json_response(FIXTURE_MESSAGES)
        if request.method == "POST" and re.fullmatch(r"/api/sessions/[^/]+/chat/stream", path):
            self.chat_inputs.append(body.get("input"))
            return await self.stream(request)
        if request.method == "GET" and re.fullmatch(r"/v1/runs/[^/]+", path):
            return web.json_response({"run_id": "run_fake", "status": "running",
                                      "pending_controls": self.pending_prompts()})
        if request.method == "POST" and path == "/v1/runs/run_fake/approval":
            self.bodies.append(("approval", body))
            self.gate.set()
            return web.json_response({"resolved": 1})
        if request.method == "POST" and path == "/v1/runs/run_fake/clarify":
            self.bodies.append(("clarify", body))
            self.gate.set()
            return web.json_response({"resolved": 1})
        if request.method == "POST" and path == "/v1/runs/run_fake/stop":
            self.stopped = True
            self.gate.set()
            return web.json_response({"ok": True})
        return web.json_response({"error": "not found"}, status=404)

    async def stream(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if self.mode == "fixture":
            for index in range(0, len(FIXTURE_SSE), 37):  # trickle the bytes to exercise incremental parsing
                await resp.write(FIXTURE_SSE[index:index + 37])
                await asyncio.sleep(0)
            await resp.write_eof()
            return resp
        if self.mode == "disconnect":
            await resp.write(_frame("run.started", {"run_id": "run_fake", "session_id": SESSION_ID}))
            await resp.write(_frame("assistant.delta", {"delta": "partial"}))
            request.transport.close()
            return resp
        await resp.write(b": keepalive\n\n")
        await resp.write(_frame("run.started", {"run_id": "run_fake", "session_id": SESSION_ID, "seq": 1}))
        await resp.write(_frame("message.started", {"message": {"id": "m1", "role": "assistant"}}))
        if self.mode == "approval":
            await resp.write(_frame("approval.request", {
                "request_id": "ap_1", "execution_generation": 7, "command": "rm -rf /tmp/x",
                "description": "Delete temp", "choices": ["once", "session", "always", "deny"],
                "message_id": "m1", "run_id": "run_fake"}))
        await asyncio.wait_for(self.gate.wait(), timeout=5)
        if self.stopped:
            await resp.write(_frame("run.cancelled", {"run_id": "run_fake", "usage": {"input_tokens": 1, "output_tokens": 0}}))
        elif self.mode == "approval":
            await resp.write(_frame("approval.settled", {"prompt_id": "ap_1", "execution_generation": 7}))
            await resp.write(_frame("run.completed", {"run_id": "run_fake", "usage": {"input_tokens": 10, "output_tokens": 2}}))
        else:
            await resp.write(_frame("clarify.settled", {"prompt_id": "cl_1", "execution_generation": 3}))
            await resp.write(_frame("assistant.delta", {"delta": "ok"}))
            await resp.write(_frame("run.completed", {"run_id": "run_fake", "usage": {"input_tokens": 5, "output_tokens": 1}}))
        await resp.write(_frame("done", {"run_id": "run_fake"}))
        await resp.write_eof()
        return resp


def _frame(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _write_profile_files(home: Path) -> None:
    (home / "profile.yaml").write_text(
        "ui_meta:\n  hermes-bots:\n    shape: blobatar\n    color: \"hsl(210 68% 58%)\"\n"
        "    title: \"\"\n    imageKind: shape\n    custom: true\n")
    (home / "config.yaml").write_text(
        "model:\n  default: hermes-root-model\n"
        "display:\n  pet:\n    enabled: true\n    slug: rabbit\n")
    (home / ".env").write_text(
        "# root profile\n"
        f"API_SERVER_KEY={ROOT_KEY}\n"
        "OTHER=1\n")
    (home / "pets" / "rabbit").mkdir(parents=True)
    (home / "pets" / "rabbit" / "spritesheet.webp").write_bytes(b"RIFFfake")

    work = home / "profiles" / "work"
    (work / "assets").mkdir(parents=True)
    (work / "profile.yaml").write_text(
        "display_name: Work Bot\n"
        "description: |\n  Helps   with\n  the   work   queue.\n"
        "ui_meta:\n  hermes-bots:\n    shape: cloud\n    color: \"#ff0000\"\n    title: Ops lead\n    imageKind: photo\n")
    (work / "config.yaml").write_text("model: gpt-test\n")
    (work / ".env").write_text(f"export API_SERVER_KEY='{WORK_KEY}'\n")
    (work / "assets" / "avatar.png").write_bytes(PNG_BYTES)

    locked = home / "profiles" / "locked"
    locked.mkdir(parents=True)
    (locked / "config.yaml").write_text("model:\n  default: x\n")


@pytest.fixture
async def fake(aiohttp_server) -> tuple[FakeHermes, str]:  # type: ignore[no-untyped-def]
    hermes = FakeHermes()
    server = await aiohttp_server(hermes.app())
    return hermes, str(server.make_url("")).rstrip("/")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "hermes"
    root.mkdir()
    _write_profile_files(root)
    return root


@pytest.fixture
async def host(home: Path, fake):  # type: ignore[no-untyped-def]
    _, base = fake
    instance = HermesHost(home, api_url=base)
    await instance.start()
    yield instance
    await instance.close()


async def _collect(handle) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return [event async for event in handle.events]


# --------------------------------------------------------------------------- .env parsing


@pytest.mark.parametrize(
    ("text", "key", "expected"),
    [
        ("API_SERVER_KEY=abc", "API_SERVER_KEY", "abc"),
        ("export API_SERVER_KEY=abc", "API_SERVER_KEY", "abc"),
        ('API_SERVER_KEY="quoted value"', "API_SERVER_KEY", "quoted value"),
        ("API_SERVER_KEY='single'", "API_SERVER_KEY", "single"),
        ("API_SERVER_KEY=bare # trailing comment", "API_SERVER_KEY", "bare"),
        ("# API_SERVER_KEY=commented\nAPI_SERVER_KEY=real", "API_SERVER_KEY", "real"),
        ("API_SERVER_KEY=first\nAPI_SERVER_KEY=second", "API_SERVER_KEY", "second"),
        ("API_SERVER_KEY=\nAPI_SERVER_KEY=later", "API_SERVER_KEY", "later"),
        ("API_SERVER_KEY=", "API_SERVER_KEY", None),
        ("OTHER=1", "API_SERVER_KEY", None),
        ("API_SERVER_KEY=a=b=c", "API_SERVER_KEY", "a=b=c"),
    ],
)
def test_read_env_value_cases(tmp_path: Path, text: str, key: str, expected: str | None) -> None:
    path = tmp_path / ".env"
    path.write_text(text + "\n")
    assert read_env_value(path, key) == expected


def test_read_env_value_missing_file(tmp_path: Path) -> None:
    assert read_env_value(tmp_path / "nope.env", "API_SERVER_KEY") is None


async def test_env_changes_are_reread(host: HermesHost, home: Path) -> None:
    assert host._api_key(home) == ROOT_KEY
    env = home / ".env"
    env.write_text("API_SERVER_KEY=rotated-key\n")
    assert host._api_key(home) == "rotated-key"
    env.write_text("API_SERVER_KEY=\n")
    assert host._api_key(home) is None


# --------------------------------------------------------------------------- bot list


async def test_bot_list_fields(host: HermesHost) -> None:
    bots = {bot["id"]: bot for bot in await host.list_bots()}
    assert set(bots) == {"default", "work", "locked"}

    root = bots["default"]
    assert root["name"] == "Hermes"
    assert root["is_default"] is True
    assert root["model"] == "hermes-root-model"
    assert root["reachable"] is True and root["locked_reason"] == ""
    assert root["avatar"] == {"kind": "shape", "shape": "blobatar", "color": "hsl(210 68% 58%)", "url": ""}
    assert root["pet"] == {"url": "", "cols": 8, "rows": 9, "cw": 192, "ch": 208, "frames": 6, "loop_ms": 1100}
    assert (root["state"], root["active"], root["last"]) == ("idle", False, None)

    work = bots["work"]
    assert work["name"] == "Work Bot"
    assert work["title"] == "Ops lead"
    assert work["description"] == "Helps with the work queue."
    assert work["model"] == "gpt-test"
    assert work["is_default"] is False
    assert work["avatar"]["kind"] == "image" and work["avatar"]["url"] == ""
    assert work["avatar"]["shape"] == "cloud" and work["avatar"]["color"] == "#ff0000"
    assert work["pet"] is None


async def test_locked_profile(host: HermesHost) -> None:
    locked = {bot["id"]: bot for bot in await host.list_bots()}["locked"]
    assert locked["name"] == "Locked"
    assert locked["reachable"] is False
    assert locked["locked_reason"] == "No API key for this profile. Run: hermes r1 setup"
    assert locked["avatar"] == {"kind": "shape", "shape": "", "color": "", "url": ""}
    with pytest.raises(HostError) as info:
        await host.run_turn("locked", None, "hi")
    assert info.value.code == "bot_locked" and info.value.status == 409


async def test_locked_profile_makes_no_upstream_request(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    with pytest.raises(HostError) as info:
        await host.list_sessions("locked", 5)
    assert info.value.code == "bot_locked"
    assert hermes.requests == []


@pytest.mark.parametrize("bad", ["..", ".", "../default", "../../etc", "a/b", "x y", "a" * 65, "", "default/../work"])
async def test_path_traversal_ids_rejected_before_filesystem(host: HermesHost, fake, bad: str) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    for call in (
        host.list_sessions(bad, 5),
        host.get_messages(bad, SESSION_ID, 5),
        host.run_turn(bad, SESSION_ID, "hi"),
    ):
        with pytest.raises(HostError) as info:
            await call
        assert info.value.code == "not_found" and info.value.status == 404
    assert await host.avatar_file(bad) is None
    assert await host.pet_file(bad) is None
    assert hermes.requests == []


# --------------------------------------------------------------------------- avatars and pets


async def test_avatar_and_pet_files(host: HermesHost, home: Path) -> None:
    work_avatar = await host.avatar_file("work")
    assert work_avatar is not None
    assert work_avatar.path == home / "profiles" / "work" / "assets" / "avatar.png"
    assert work_avatar.content_type == "image/png"
    assert await host.avatar_file("default") is None

    pet = await host.pet_file("default")
    assert pet is not None and pet.path.name == "spritesheet.webp" and pet.content_type == "image/webp"
    assert await host.pet_file("work") is None
    assert await host.pet_file("locked") is None


# --------------------------------------------------------------------------- sessions and messages


async def test_sessions_are_filtered_and_sorted(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    sessions = await host.list_sessions("default", 10)
    assert [s["id"] for s in sessions] == ["api_s2", "api_s1"]
    assert sessions[1] == {"id": "api_s1", "title": "Hello there", "preview": "Hello there",
                           "last_active": 200.0, "message_count": 3}
    assert sessions[0]["title"] == "Newest"
    listing = next(r for r in hermes.requests if r["path"] == "/api/sessions")
    assert listing["query"]["source"] == "api_server"
    assert int(listing["query"]["limit"]) == 30


async def test_named_profile_uses_prefix_and_its_own_key(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    await host.list_sessions("work", 5)
    assert hermes.requests[-1]["profile"] == "work"
    assert hermes.requests[-1]["path"] == "/api/sessions"


async def test_messages_are_folded(host: HermesHost) -> None:
    messages = await host.get_messages("default", SESSION_ID, 30)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "Use the terminal tool to run `echo r1-fixture` and then reply with exactly: done."
    assistant = messages[1]
    assert assistant["text"] == "done."
    assert [(t["name"], t["ok"]) for t in assistant["tools"]] == [("skill_view", True), ("terminal", True)]
    assert assistant["tools"][0]["preview"] == "omh-terminal"
    assert assistant["tools"][1]["preview"] == "echo r1-fixture"


async def test_messages_limit_keeps_latest(host: HermesHost) -> None:
    messages = await host.get_messages("default", SESSION_ID, 1)
    assert [m["role"] for m in messages] == ["assistant"]


def test_fold_messages_failed_tool_and_content_parts() -> None:
    rows = [
        {"id": 1, "role": "user", "content": [{"type": "text", "text": "do it"}], "timestamp": 1.0},
        {"id": 2, "role": "user", "content": "   ", "timestamp": 2.0},
        {"id": 3, "role": "assistant", "content": "", "timestamp": 3.0,
         "tool_calls": [{"id": "c1", "function": {"name": "terminal", "arguments": "{\"command\": \"false\"}"}}]},
        {"id": 4, "role": "tool", "tool_call_id": "c1", "content": "{\"output\": \"\", \"exit_code\": 1, \"error\": null}"},
        {"id": 5, "role": "session_meta", "content": None},
        {"id": 6, "role": "assistant", "content": [{"type": "text", "text": "failed"}], "timestamp": 4.0},
    ]
    folded = _fold_messages(rows)
    assert [m["role"] for m in folded] == ["user", "assistant"]
    assert folded[0]["text"] == "do it"
    assert folded[1]["text"] == "failed"
    assert folded[1]["tools"] == [{"name": "terminal", "ok": False, "preview": "false"}]


def test_result_ok_rules() -> None:
    assert _result_ok('{"error": "boom"}') is False
    assert _result_ok('{"exit_code": 2}') is False
    assert _result_ok('{"exit_code": 0, "error": null}') is True
    assert _result_ok("plain text") is True
    assert _result_ok(None) is True


# --------------------------------------------------------------------------- turns


async def test_run_turn_replays_fixture(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    handle = await host.run_turn("default", SESSION_ID, "Use the terminal tool")
    assert handle.session_id == SESSION_ID
    events = await _collect(handle)

    kinds = [(e["t"], e.get("phase")) for e in events]
    assert kinds == [
        ("start", None), ("tool", "start"), ("tool", "end"), ("tool", "start"), ("tool", "end"),
        ("delta", None), ("delta", None), ("end", None),
    ]
    assert events[0]["session_id"] == SESSION_ID
    assert events[1]["name"] == "skill_view" and events[1]["preview"] == "omh-terminal"
    assert events[2]["name"] == "skill_view" and events[2]["ok"] is True and events[2]["ms"] > 0
    assert events[3]["id"] != events[1]["id"]
    assert events[4]["name"] == "terminal" and events[4]["ok"] is True
    assert events[5]["text"] == "\n\ndone" and events[6]["text"] == "."

    end = events[-1]
    assert end["status"] == "completed"
    assert end["text"] == "done."
    assert end["error"] == ""
    assert end["usage"] == {"input": 98735, "output": 47}
    assert hermes.chat_inputs[-1] == "Use the terminal tool"


async def test_run_turn_creates_session(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    text = "Use the terminal tool to run `echo r1-fixture` and then reply with exactly: done."
    handle = await host.run_turn("default", None, text)
    assert handle.session_id == SESSION_ID
    assert hermes.created_titles == [text[:40]]
    events = await _collect(handle)
    assert events[-1]["t"] == "end" and events[-1]["status"] == "completed"


async def test_upstream_auth_failure(home: Path, fake) -> None:  # type: ignore[no-untyped-def]
    _, base = fake
    (home / ".env").write_text("API_SERVER_KEY=wrong-key\n")
    host = HermesHost(home, api_url=base)
    try:
        with pytest.raises(HostError) as info:
            await host.list_sessions("default", 5)
        assert info.value.code == "upstream_auth" and info.value.status == 502
        with pytest.raises(HostError) as info:
            await host.run_turn("default", SESSION_ID, "hi")
        assert info.value.code == "upstream_auth"
    finally:
        await host.close()


async def test_upstream_unavailable(home: Path) -> None:
    host = HermesHost(home, api_url="http://127.0.0.1:1")
    try:
        with pytest.raises(HostError) as info:
            await host.list_sessions("default", 5)
        assert info.value.code == "upstream_unavailable" and info.value.status == 502
        with pytest.raises(HostError) as info:
            await host.run_turn("default", SESSION_ID, "hi")
        assert info.value.code == "upstream_unavailable"
    finally:
        await host.close()


async def test_mid_stream_disconnect_ends_failed_with_text(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    hermes.mode = "disconnect"
    events = await _collect(await host.run_turn("default", SESSION_ID, "hi"))
    assert [e["t"] for e in events] == ["start", "delta", "end"]
    assert events[-1]["status"] == "failed"
    assert events[-1]["text"] == "partial"
    assert events[-1]["error"]


async def test_approval_round_trip(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    hermes.mode = "approval"
    handle = await host.run_turn("default", SESSION_ID, "delete it")
    events: list[dict[str, Any]] = []
    async for event in handle.events:
        events.append(event)
        if event["t"] == "ask":
            assert event["request_id"] == "ap_1" and event["kind"] == "approval"
            assert [c["id"] for c in event["choices"]] == ["once", "session", "always", "deny"]
            assert event["choices"][0]["label"] == "Allow once"
            assert "Delete temp" in event["detail"]
            await host.respond("default", SESSION_ID, "ap_1", choice="once")

    asks = [e for e in events if e["t"] == "ask"]
    assert len(asks) == 1  # the poll does not duplicate the stream's ask
    assert {"t": "ask_done", "request_id": "ap_1"} in events
    assert events[-1]["t"] == "end" and events[-1]["status"] == "completed"
    assert hermes.bodies == [("approval", {"request_id": "ap_1", "execution_generation": 7, "choice": "once"})]


async def test_clarify_polled_from_pending_controls(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    hermes.mode = "clarify"
    handle = await host.run_turn("default", SESSION_ID, "pick")
    events: list[dict[str, Any]] = []
    async for event in handle.events:
        events.append(event)
        if event["t"] == "ask":
            assert event["kind"] == "clarify" and event["request_id"] == "cl_1"
            assert [c["id"] for c in event["choices"]] == ["alpha", "beta"]
            await host.respond("default", SESSION_ID, "cl_1", answer="alpha")

    assert any(e["t"] == "ask_done" and e["request_id"] == "cl_1" for e in events)
    assert events[-1]["status"] == "completed" and events[-1]["text"] == "ok"
    assert hermes.bodies == [("clarify", {"request_id": "cl_1", "execution_generation": 3, "answer": "alpha"})]


async def test_stop_turn_cancels(host: HermesHost, fake) -> None:  # type: ignore[no-untyped-def]
    hermes, _ = fake
    hermes.mode = "approval"
    handle = await host.run_turn("default", SESSION_ID, "delete it")
    events: list[dict[str, Any]] = []
    async for event in handle.events:
        events.append(event)
        if event["t"] == "ask":
            await host.stop_turn("default", SESSION_ID)
    assert hermes.stopped is True
    assert events[-1]["t"] == "end" and events[-1]["status"] == "cancelled"


async def test_stop_turn_without_run_is_noop(host: HermesHost) -> None:
    await host.stop_turn("default", "api_unknown")


async def test_respond_without_turn_is_refused(host: HermesHost) -> None:
    with pytest.raises(HostError) as info:
        await host.respond("default", "api_unknown", "ap_1", choice="once")
    assert info.value.code == "ask_gone"


# --------------------------------------------------------------------------- SSE parser


async def _chunks(*parts: bytes):  # type: ignore[no-untyped-def]
    for part in parts:
        yield part


async def test_sse_parser_handles_crlf_comments_and_multiline() -> None:
    raw = (b": keepalive\r\n\r\nevent: a\r\ndata: one\r\ndata: two\r\n\r\n"
           b"event: b\ndata: {\"x\": 1}\n\n: ping\n\nevent: c\ndata: split")
    frames = [frame async for frame in iter_sse(_chunks(raw[:17], raw[17:61], raw[61:]))]
    assert frames == [("a", "one\ntwo"), ("b", '{"x": 1}'), ("c", "split")]


def test_fixture_stream_parses_into_expected_frames() -> None:
    async def run() -> list[str | None]:
        return [event async for event, _ in iter_sse(_chunks(FIXTURE_SSE))]

    names = asyncio.run(run())
    assert names[0] == "run.started" and names[-1] == "done"
    assert names.count("tool.started") == 2 and names.count("tool.completed") == 2


# --------------------------------------------------------------------------- voice


async def test_voice_unavailable_without_hermes(host: HermesHost) -> None:
    capabilities = host.voice_capabilities()
    assert isinstance(capabilities["stt"], bool) and isinstance(capabilities["tts"], bool)
    host._voice_probed, host._stt, host._tts = True, None, None
    with pytest.raises(HostError) as info:
        await host.transcribe(Path("/tmp/none.ogg"), "audio/ogg")
    assert info.value.code == "stt_unavailable" and info.value.status == 501
    with pytest.raises(HostError) as info:
        await host.synthesize("hello", Path("/tmp/none-out"))
    assert info.value.code == "tts_unavailable" and info.value.status == 501


async def test_transcribe_uses_stt_result(host: HermesHost, tmp_path: Path) -> None:
    host._voice_probed = True
    host._stt = lambda path: {"success": True, "transcript": "  hello there  ", "error": None}
    audio = tmp_path / "in.ogg"
    audio.write_bytes(b"OggS")
    assert await host.transcribe(audio, "audio/ogg") == "hello there"


async def test_synthesize_moves_file_into_out_dir(host: HermesHost, tmp_path: Path) -> None:
    source_dir = tmp_path / "cache"
    source_dir.mkdir()
    source = source_dir / "tts_1.mp3"
    source.write_bytes(b"ID3")

    def fake_tts(text: str, **_: Any) -> str:
        assert text == "hello"
        return json.dumps({"success": True, "file_path": str(source)})

    host._voice_probed = True
    host._tts = fake_tts
    out_dir = tmp_path / "audio"
    asset = await host.synthesize("hello", out_dir)
    assert asset.path.parent == out_dir and asset.path.suffix == ".mp3"
    assert asset.path.read_bytes() == b"ID3"
    assert not source.exists()
    assert asset.content_type == "audio/mpeg"


async def test_synthesize_content_types(host: HermesHost, tmp_path: Path) -> None:
    host._voice_probed = True
    for ext, expected in ((".ogg", "audio/ogg"), (".wav", "audio/wav")):
        source = tmp_path / f"src{ext}"
        source.write_bytes(b"x")
        host._tts = lambda text, _s=source, **__: json.dumps({"success": True, "file_path": str(_s)})
        asset = await host.synthesize("hi", tmp_path / "out")
        assert asset.content_type == expected
