"""Regression coverage for the authenticated Fleet Now snapshot."""

import asyncio

import pytest

from hermes_r1.bridge.mock_host import MockHost
from hermes_r1.bridge.server import create_app
from hermes_r1.bridge.store import Store


@pytest.fixture
async def fleet_client(aiohttp_client, tmp_path):
    store = Store(tmp_path / "state")
    static = tmp_path / "static"
    (static / "app").mkdir(parents=True)
    (static / "app" / "index.html").write_text("<!doctype html><title>r1</title>")
    client = await aiohttp_client(create_app(MockHost(speed=0), store, static_dir=static))
    return client, store


def _admin(store):
    return {"Authorization": f"Bearer {store.admin_token}"}


async def _pair(client, store, name):
    code = (await (await client.post("/api/v1/admin/pair", headers=_admin(store))).json())["code"]
    data = await (await client.post("/api/v1/pair", json={"code": code, "name": name})).json()
    return {"Authorization": f"Bearer {data['token']}"}


async def _fleet(client, headers):
    response = await client.get("/api/v1/fleet", headers=headers)
    assert response.status == 200, await response.text()
    return await response.json()


async def _wait_for(client, headers, predicate, timeout=3):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        data = await _fleet(client, headers)
        if predicate(data):
            return data
        await asyncio.sleep(0.01)
    raise AssertionError("Fleet Now did not reach the expected state")


async def test_fleet_requires_device_auth(fleet_client):
    client, _ = fleet_client
    response = await client.get("/api/v1/fleet")
    assert response.status == 401
    assert (await response.json())["error"]["code"] == "unauthorized"


async def test_fleet_ask_lifecycle_and_response(fleet_client):
    client, store = fleet_client
    device = await _pair(client, store, "owner")

    response = await client.post(
        "/api/v1/bots/default/turns", json={"text": "please approve this"}, headers=device,
    )
    assert response.status == 200
    turn_id = (await response.json())["turn_id"]

    snapshot = await _wait_for(client, device, lambda data: bool(data["needs_you"]))
    assert set(snapshot) == {"needs_you", "working", "finished", "gateways"}
    assert all(set(gateway) == {"id", "name", "status"} for gateway in snapshot["gateways"])
    assert all(gateway["status"] in {"online", "offline", "unknown"}
               for gateway in snapshot["gateways"])
    assert {gateway["id"] for gateway in snapshot["gateways"]} == {"local"}
    entry, = [item for item in snapshot["needs_you"] if item["turn_id"] == turn_id]
    assert set(entry) == {"turn_id", "bot_id", "ask"}
    assert entry["bot_id"] == "default"
    ask = entry["ask"]
    assert ask["t"] == "ask"
    assert set(ask) == {"t", "request_id", "kind", "title", "detail", "choices"}
    assert ask["kind"] == "approval"
    assert ask["choices"]

    # A different paired device cannot see the prompt or answer its turn.
    other = await _pair(client, store, "other")
    other_snapshot = await _fleet(client, other)
    assert all(item["turn_id"] != turn_id for item in other_snapshot["needs_you"])
    assert all(item["turn_id"] != turn_id for item in other_snapshot["working"])
    response = await client.post(
        f"/api/v1/turns/{turn_id}/respond",
        json={"request_id": ask["request_id"], "choice": "once"},
        headers=other,
    )
    assert response.status == 404

    response = await client.post(
        f"/api/v1/turns/{turn_id}/respond",
        json={"request_id": ask["request_id"], "choice": "once"},
        headers=device,
    )
    assert response.status == 200
    assert (await response.json()) == {"ok": True}

    # The existing response path must resolve the prompt and let the turn end.
    events = []
    for _ in range(50):
        response = await client.get(
            f"/api/v1/turns/{turn_id}/events", params={"after": len(events), "wait": 0.1}, headers=device,
        )
        data = await response.json()
        events.extend(data["events"])
        if data["done"]:
            break
    assert [event["t"] for event in events].count("ask_done") == 1
    assert events[-1]["t"] == "end"

    after = await _wait_for(
        client, device,
        lambda data: all(item["turn_id"] != turn_id for item in data["needs_you"] + data["working"]),
    )
    assert all(item["turn_id"] != turn_id for item in after["needs_you"] + after["working"])


async def test_fleet_lists_only_calling_device_turns(fleet_client):
    client, store = fleet_client
    owner = await _pair(client, store, "owner")
    other = await _pair(client, store, "other")

    response = await client.post(
        "/api/v1/bots/default/turns", json={"text": "please approve this"}, headers=owner,
    )
    turn_id = (await response.json())["turn_id"]
    own = await _wait_for(client, owner, lambda data: bool(data["needs_you"]))
    assert any(item["turn_id"] == turn_id for item in own["needs_you"])

    foreign = await _fleet(client, other)
    assert all(item["turn_id"] != turn_id for item in foreign["needs_you"])
    assert all(item["turn_id"] != turn_id for item in foreign["working"])
    assert all(item.get("turn_id") != turn_id for item in foreign["finished"])

    await client.post(f"/api/v1/turns/{turn_id}/stop", headers=owner)


async def test_finished_direct_results_are_device_private_persistent_and_exact(fleet_client):
    client, store = fleet_client
    owner = await _pair(client, store, "owner")
    other = await _pair(client, store, "other")
    response = await client.post(
        "/api/v1/bots/default/turns", json={"text": "finished-result"}, headers=owner,
    )
    turn_id = (await response.json())["turn_id"]
    snapshot = await _wait_for(client, owner, lambda data: bool(data["finished"]))
    item, = snapshot["finished"]
    assert item["kind"] == "chat"
    assert item["bot"] == "default"
    assert item["turn_id"] == turn_id
    assert item["session_id"]
    assert item["status"] == "completed"
    assert "finished-result" in item["text"]
    assert item["ts"] > 0
    assert (await _fleet(client, other))["finished"] == []

    reopened = Store(store.state_dir)
    device_id = reopened.device_for_token(owner["Authorization"].split()[1])["id"]
    assert reopened.finished(device_id)[0] == item
