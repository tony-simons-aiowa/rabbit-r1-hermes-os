"""End-to-end routing across local Hermes and two independent gateway APIs."""

from __future__ import annotations

import argparse
import asyncio
import json

import pytest
from aiohttp import web

from hermes_r1 import cli
from hermes_r1.bridge.__main__ import _build_host, build_parser
from hermes_r1.bridge.gateways import GatewayHost, load_gateways
from hermes_r1.bridge.mock_host import MockHost
from hermes_r1.bridge.server import create_app
from hermes_r1.bridge.store import Store


class FakeGateway:
    """Small Hermes API server that records which credentials and data were routed."""

    def __init__(self, key: str, label: str):
        self.key, self.label = key, label
        self.requests: list[tuple[str, str]] = []
        self.controls: list[tuple[str, dict]] = []
        self.control_received = asyncio.Event()

    def app(self):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.dispatch)
        return app

    async def dispatch(self, request: web.Request):
        self.requests.append((request.method, request.path))
        if request.headers.get("Authorization") != f"Bearer {self.key}":
            return web.json_response({"error": "unauthorized"}, status=401)
        if request.method == "GET" and request.path == "/api/sessions":
            return web.json_response({"data": [{
                "id": "shared-session", "source": "api_server", "title": self.label,
                "preview": self.label, "last_active": 200.0, "started_at": 100.0,
                "message_count": 1,
            }]})
        if request.method == "GET" and request.path.endswith("/messages"):
            return web.json_response({"data": [{"role": "assistant", "content": self.label,
                                                  "created_at": 100.0}]})
        if request.method == "POST" and request.path == "/api/sessions":
            return web.json_response({"session": {"id": "shared-session"}})
        if request.method == "POST" and request.path.endswith("/chat/stream"):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            frames = [
                {"event": "run.started", "data": {"run_id": f"run-{self.label}",
                                                     "session_id": "shared-session"}},
                {"event": "approval.request", "data": {"request_id": "shared-request",
                                                            "execution_generation": 1,
                                                            "command": "run command",
                                                            "choices": ["once", "deny"]}},
            ]
            for frame in frames:
                await response.write(f"event: {frame['event']}\ndata: {json.dumps(frame['data'])}\n\n".encode())
            await asyncio.wait_for(self.control_received.wait(), timeout=5)
            await response.write(f"event: approval.settled\ndata: {json.dumps({'prompt_id': 'shared-request', 'execution_generation': 1})}\n\n".encode())
            await response.write(f"event: assistant.delta\ndata: {json.dumps({'delta': f'reply-{self.label}'})}\n\n".encode())
            await response.write(f"event: run.completed\ndata: {json.dumps({'run_id': f'run-{self.label}'})}\n\n".encode())
            await response.write(f"event: done\ndata: {json.dumps({'run_id': f'run-{self.label}'})}\n\n".encode())
            await response.write_eof()
            return response
        if request.method == "POST" and request.path.endswith("/approval"):
            self.controls.append((request.path, await request.json()))
            self.control_received.set()
            return web.json_response({"resolved": 1})
        if request.method == "POST" and request.path.endswith("/stop"):
            self.controls.append((request.path, await request.json()))
            self.control_received.set()
            return web.json_response({"ok": True})
        return web.json_response({"error": "not found"}, status=404)


async def pair(client, store):
    admin = {"Authorization": f"Bearer {store.admin_token}"}
    code = (await (await client.post("/api/v1/admin/pair", headers=admin)).json())["code"]
    result = await client.post("/api/v1/pair", json={"code": code})
    return {"Authorization": f"Bearer {(await result.json())['token']}"}


async def test_native_serve_passes_gateways_file_to_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "test-key")
    path = tmp_path / "gateways.json"
    path.write_text(json.dumps([{
        "id": "office", "name": "Office", "url": "https://hermes.example/r1",
        "key_env": "TEST_GATEWAY_KEY",
    }]))
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    command = parser.parse_args(["serve", "--gateways-file", str(path), "--hermes-home", str(tmp_path)])
    args = build_parser().parse_args(cli._serve_argv(command))
    host = _build_host(args)
    assert isinstance(host, GatewayHost)
    assert [bot["id"] for bot in await host.list_bots()] == ["default", "office:default"]


@pytest.mark.parametrize("url", [
    "http://hermes.example", "https://user:pass@hermes.example", "https://hermes.example?",
])
def test_rejects_insecure_or_ambiguous_gateway_urls(tmp_path, monkeypatch, url):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "test-key")
    path = tmp_path / "gateways.json"
    path.write_text(json.dumps([{
        "id": "office", "name": "Office", "url": url, "key_env": "TEST_GATEWAY_KEY",
    }]))
    with pytest.raises(ValueError) as error:
        load_gateways(path)
    assert url not in str(error.value)


async def test_two_gateways_route_sessions_turns_and_keep_credentials_private(
    aiohttp_client, aiohttp_server, tmp_path,
):
    alpha, beta = FakeGateway("alpha-secret", "Alpha"), FakeGateway("beta-secret", "Beta")
    alpha_server = await aiohttp_server(alpha.app())
    beta_server = await aiohttp_server(beta.app())
    home = tmp_path / "hermes"
    home.mkdir()
    host = GatewayHost(MockHost(speed=0), home, [
        {"id": "alpha", "name": "Alpha Gateway", "url": str(alpha_server.make_url("/"))[:-1], "key": "alpha-secret"},
        {"id": "beta", "name": "Beta Gateway", "url": str(beta_server.make_url("/"))[:-1], "key": "beta-secret"},
    ])
    store = Store(tmp_path / "state")
    client = await aiohttp_client(create_app(host, store, static_dir=tmp_path / "static"))
    dev = await pair(client, store)

    bootstrap = await (await client.get("/api/v1/bootstrap", headers=dev)).json()
    bots = bootstrap["bots"]
    assert {b["id"] for b in bots} >= {"default", "alpha:default", "beta:default"}
    assert {b["gateway_id"] for b in bots if b["id"].endswith(":default")} == {"alpha", "beta"}
    assert next(b for b in bots if b["id"] == "alpha:default")["gateway_name"] == "Alpha Gateway"
    public = json.dumps(bootstrap)
    for secret in ("alpha-secret", "beta-secret", str(alpha_server.make_url("/")), str(beta_server.make_url("/"))):
        assert secret not in public

    # The upstreams intentionally return the same session ID. Bot namespace owns the route.
    for bot, expected in (("alpha:default", "Alpha"), ("beta:default", "Beta")):
        data = await (await client.get(f"/api/v1/bots/{bot}/sessions", headers=dev)).json()
        assert data["sessions"][0]["id"] == "shared-session"
        messages = await (await client.get(
            f"/api/v1/bots/{bot}/sessions/shared-session/messages", headers=dev,
        )).json()
        assert expected in json.dumps(messages)

    # Both upstreams receive the identical session ID and approval request ID.
    turn_ids = []
    for bot in ("alpha:default", "beta:default"):
        response = await client.post(f"/api/v1/bots/{bot}/turns", headers=dev,
                                     json={"text": "hello", "session_id": "shared-session"})
        assert response.status == 200
        turn_ids.append((await response.json())["turn_id"])
    async def wait_for_ask(tid):
        for _ in range(20):
            result = await (await client.get(f"/api/v1/turns/{tid}/events?wait=0.1", headers=dev)).json()
            if any(event["t"] == "ask" for event in result["events"]):
                return
        raise AssertionError("approval event did not arrive")

    await asyncio.gather(*(wait_for_ask(tid) for tid in turn_ids))
    response = await client.post(f"/api/v1/turns/{turn_ids[0]}/respond", headers=dev,
                                 json={"request_id": "shared-request", "choice": "once"})
    assert response.status == 200
    stopped = await client.post(f"/api/v1/turns/{turn_ids[1]}/stop", headers=dev)
    assert stopped.status == 200
    turn_events = []
    for tid in turn_ids:
        after, events = 0, []
        for _ in range(20):
            result = await (await client.get(
                f"/api/v1/turns/{tid}/events?after={after}&wait=0.1", headers=dev,
            )).json()
            events.extend(result["events"])
            after = result["next"]
            if result["done"]:
                break
        assert result["done"]
        turn_events.append(json.dumps(events))
    assert "reply-Alpha" in turn_events[0] and "reply-Beta" not in turn_events[0]
    assert "reply-Beta" in turn_events[1] and "reply-Alpha" not in turn_events[1]
    assert any(path.endswith("/chat/stream") for _, path in alpha.requests)
    assert any(path.endswith("/chat/stream") for _, path in beta.requests)
    assert alpha.controls == [("/v1/runs/run-Alpha/approval", {
        "request_id": "shared-request", "execution_generation": 1, "choice": "once",
    })]
    assert beta.controls == [("/v1/runs/run-Beta/stop", {})]

    # Voice capability remains the local bridge capability, independent of gateways.
    assert bootstrap["voice"] == host.voice_capabilities()
