import asyncio
import json
import time

import pytest

from hermes_r1.bridge.auth import sign_asset
from hermes_r1.bridge.mock_host import MockHost
from hermes_r1.bridge.server import create_app
from hermes_r1.bridge.store import Store

BOT_KEYS = {"id", "name", "title", "description", "model", "is_default", "reachable",
            "locked_reason", "avatar", "pet", "state", "active", "last"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


@pytest.fixture
def static_dir(tmp_path):
    root = tmp_path / "static"
    (root / "app").mkdir(parents=True)
    (root / "app" / "index.html").write_text("<!doctype html><title>r1</title>")
    (root / "app" / "app.js").write_text("console.log(1)")
    return root


@pytest.fixture
async def client(aiohttp_client, store, static_dir):
    app = create_app(MockHost(speed=0, voice=True), store, static_dir=static_dir)
    return await aiohttp_client(app)


def admin(store):
    return {"Authorization": f"Bearer {store.admin_token}"}


async def pair_device(client, store, name="test r1"):
    resp = await client.post("/api/v1/admin/pair", headers=admin(store))
    code = (await resp.json())["code"]
    resp = await client.post("/api/v1/pair", json={"code": code, "name": name})
    assert resp.status == 200
    token = (await resp.json())["token"]
    return {"Authorization": f"Bearer {token}"}


async def poll_until(client, dev, tid, pred, after=0, timeout=10):
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await client.get(f"/api/v1/turns/{tid}/events", params={"after": after, "wait": 2}, headers=dev)
        data = await resp.json()
        events += data["events"]
        after = data["next"]
        if pred(events) or data["done"]:
            return events, after
    raise AssertionError("timed out waiting for events")


async def run_turn(client, dev, bot, text, speak=False):
    resp = await client.post(f"/api/v1/bots/{bot}/turns", json={"text": text, "session_id": None, "speak": speak},
                             headers=dev)
    assert resp.status == 200, await resp.text()
    tid = (await resp.json())["turn_id"]
    events, _ = await poll_until(client, dev, tid, lambda e: any(x["t"] == "end" for x in e))
    return tid, events


# ---- health, pairing, auth ----------------------------------------------

async def test_health(client):
    data = await (await client.get("/api/v1/health")).json()
    assert data == {"ok": True, "name": "hermes-r1", "version": "0.1.0", "mock": True}


async def test_pair_happy_path_and_bad_code(client, store):
    resp = await client.post("/api/v1/admin/pair", headers=admin(store))
    code = (await resp.json())["code"]
    resp = await client.post("/api/v1/pair", json={"code": code.lower(), "name": "  " + "n" * 60 + "  "})
    assert resp.status == 200
    data = await resp.json()
    assert data["token"] and data["device"]["name"] == "n" * 40 and data["device"]["id"].startswith("dev_")
    resp = await client.post("/api/v1/pair", json={"code": "ZZZZZZZZ"})
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "bad_code"


async def test_pair_code_single_use_and_default_name(client, store):
    code = (await (await client.post("/api/v1/admin/pair", headers=admin(store))).json())["code"]
    assert (await client.post("/api/v1/pair", json={"code": code})).status == 200
    resp = await client.post("/api/v1/pair", json={"code": code})
    assert resp.status == 400


async def test_pair_rate_limited_after_five_failures(client):
    for _ in range(5):
        resp = await client.post("/api/v1/pair", json={"code": "WRONG"})
        assert resp.status == 400
    resp = await client.post("/api/v1/pair", json={"code": "WRONG"})
    assert resp.status == 429
    assert (await resp.json())["error"]["code"] == "rate_limited"


async def test_device_routes_need_token(client):
    resp = await client.get("/api/v1/bootstrap")
    assert resp.status == 401
    body = await resp.json()
    assert body["error"]["code"] == "unauthorized"
    resp = await client.get("/api/v1/bootstrap", headers={"Authorization": "Bearer nope"})
    assert resp.status == 401


async def test_bootstrap_shape(client, store):
    dev = await pair_device(client, store)
    data = await (await client.get("/api/v1/bootstrap", headers=dev)).json()
    assert set(data) == {"server", "device", "config", "config_rev", "bots", "voice"}
    assert data["server"] == {"version": "0.1.0", "mock": True}
    assert data["device"]["name"] == "test r1"
    assert data["voice"] == {"stt": True, "tts": True}
    assert data["bots"] and all(set(b) == BOT_KEYS for b in data["bots"])
    vault = next(b for b in data["bots"] if b["id"] == "vault")
    assert vault["reachable"] is False and "hermes r1 setup" in vault["locked_reason"]
    assert data["config"]["theme"]["preset"] == "nous"


async def test_bots_route_has_rev(client, store):
    dev = await pair_device(client, store)
    data = await (await client.get("/api/v1/bots", headers=dev)).json()
    assert data["config_rev"] == store.config_rev and len(data["bots"]) >= 1


# ---- sessions and turns --------------------------------------------------

async def test_sessions_and_messages(client, store):
    dev = await pair_device(client, store)
    sessions = (await (await client.get("/api/v1/bots/default/sessions?limit=1", headers=dev)).json())["sessions"]
    assert len(sessions) == 1
    sid = sessions[0]["id"]
    msgs = (await (await client.get(f"/api/v1/bots/default/sessions/{sid}/messages?limit=5", headers=dev)).json())
    assert msgs["messages"] and msgs["messages"][-1]["role"] == "assistant"
    locked = await client.get("/api/v1/bots/vault/sessions", headers=dev)
    assert locked.status == 409


async def test_full_turn_via_long_poll(client, store):
    dev = await pair_device(client, store)
    tid, events = await run_turn(client, dev, "coder", "hello from the test")
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert seqs == list(range(1, len(seqs) + 1))
    assert [e["t"] for e in events].count("end") == 1
    assert events[0]["t"] == "start" and events[-1]["status"] == "completed"
    assert any(e["t"] == "tool" for e in events)
    bots = (await (await client.get("/api/v1/bots", headers=dev)).json())["bots"]
    coder = next(b for b in bots if b["id"] == "coder")
    assert coder["state"] == "idle" and coder["active"] is True
    assert coder["last"]["text"].startswith("You asked")


async def test_after_cursor_semantics(client, store):
    dev = await pair_device(client, store)
    tid, events = await run_turn(client, dev, "default", "cursor test")
    resp = await client.get(f"/api/v1/turns/{tid}/events", params={"after": events[-1]["seq"], "wait": 0},
                            headers=dev)
    data = await resp.json()
    assert data["events"] == [] and data["next"] == events[-1]["seq"] and data["done"] is True


async def test_long_poll_wait_is_bounded(client, store):
    dev = await pair_device(client, store)
    tid, _ = await run_turn(client, dev, "default", "bounded")
    started = time.monotonic()
    resp = await client.get(f"/api/v1/turns/{tid}/events", params={"after": 999, "wait": 0.2}, headers=dev)
    assert (await resp.json())["events"] == []
    assert time.monotonic() - started < 3


async def test_approval_flow_over_http(client, store):
    dev = await pair_device(client, store)
    resp = await client.post("/api/v1/bots/default/turns", json={"text": "please approve this"}, headers=dev)
    tid = (await resp.json())["turn_id"]
    events, after = await poll_until(client, dev, tid, lambda e: any(x["t"] == "ask" for x in e))
    ask = next(e for e in events if e["t"] == "ask")
    resp = await client.post(f"/api/v1/turns/{tid}/respond", json={"request_id": ask["request_id"], "choice": "once"},
                             headers=dev)
    assert resp.status == 200 and (await resp.json()) == {"ok": True}
    events2, _ = await poll_until(client, dev, tid, lambda e: any(x["t"] == "end" for x in e), after=after)
    all_events = events + events2
    assert [e["t"] for e in all_events].count("ask_done") == 1
    assert all_events[-1]["status"] == "completed"


async def test_respond_validation(client, store):
    dev = await pair_device(client, store)
    tid, _ = await run_turn(client, dev, "default", "fine")
    resp = await client.post(f"/api/v1/turns/{tid}/respond", json={"request_id": "r"}, headers=dev)
    assert resp.status == 400
    resp = await client.post(f"/api/v1/turns/{tid}/respond", json={"request_id": "r", "choice": "once"},
                             headers=dev)
    assert resp.status == 409


async def test_stop_turn_is_cancelled(client, store):
    dev = await pair_device(client, store)
    resp = await client.post("/api/v1/bots/default/turns", json={"text": "approve and wait"}, headers=dev)
    tid = (await resp.json())["turn_id"]
    await poll_until(client, dev, tid, lambda e: any(x["t"] == "ask" for x in e))
    resp = await client.post(f"/api/v1/turns/{tid}/stop", headers=dev)
    assert (await resp.json()) == {"ok": True}
    events, _ = await poll_until(client, dev, tid, lambda e: any(x["t"] == "end" for x in e))
    assert events[-1]["status"] == "cancelled"


async def test_busy_returns_429(client, store):
    dev = await pair_device(client, store)
    resp = await client.post("/api/v1/bots/default/turns", json={"text": "approve it"}, headers=dev)
    tid = (await resp.json())["turn_id"]
    resp = await client.post("/api/v1/bots/default/turns", json={"text": "second"}, headers=dev)
    assert resp.status == 429 and (await resp.json())["error"]["code"] == "busy"
    await client.post(f"/api/v1/turns/{tid}/stop", headers=dev)


async def test_locked_bot_turn_409(client, store):
    dev = await pair_device(client, store)
    resp = await client.post("/api/v1/bots/vault/turns", json={"text": "hi"}, headers=dev)
    assert resp.status == 409 and (await resp.json())["error"]["code"] == "bot_locked"


async def test_turns_are_device_scoped(client, store):
    dev_a = await pair_device(client, store, "a")
    dev_b = await pair_device(client, store, "b")
    resp = await client.post("/api/v1/bots/default/turns", json={"text": "approve"}, headers=dev_a)
    tid = (await resp.json())["turn_id"]
    resp = await client.get(f"/api/v1/turns/{tid}/events", params={"wait": 0}, headers=dev_b)
    assert resp.status == 404
    await client.post(f"/api/v1/turns/{tid}/stop", headers=dev_a)


async def test_turn_body_validation_and_bad_json(client, store):
    dev = await pair_device(client, store)
    resp = await client.post("/api/v1/bots/default/turns", data="{nope", headers=dev)
    assert resp.status == 400 and (await resp.json())["error"]["code"] == "bad_json"
    resp = await client.post("/api/v1/bots/default/turns", json={"text": "   "}, headers=dev)
    assert resp.status == 400
    resp = await client.post("/api/v1/bots/default/turns", data="x" * (70 * 1024),
                             headers={**dev, "Content-Type": "application/json"})
    assert resp.status == 413 and (await resp.json())["error"]["code"] == "too_large"


async def test_speak_turn_has_audio_before_end(client, store):
    dev = await pair_device(client, store)
    _, events = await run_turn(client, dev, "default", "read this aloud", speak=True)
    kinds = [e["t"] for e in events]
    assert kinds.index("speech") < kinds.index("end")
    speech = next(e for e in events if e["t"] == "speech")
    resp = await client.get(speech["url"])
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("audio/wav")
    assert resp.headers["Cache-Control"] == "private, max-age=3600"
    assert len(await resp.read()) > 44


# ---- voice ----------------------------------------------------------------

async def test_transcribe_and_speak(client, store):
    dev = await pair_device(client, store)
    resp = await client.post("/api/v1/voice/transcribe", data=b"\x1aE\xdf\xa3fake",
                             headers={**dev, "Content-Type": "audio/webm"})
    assert (await resp.json()) == {"text": "This is a mock transcription."}
    resp = await client.post("/api/v1/voice/speak", json={"text": "hi"}, headers=dev)
    data = await resp.json()
    assert data["mime"] == "audio/wav" and data["url"].startswith("/api/v1/assets/audio/")
    assert (await client.post("/api/v1/voice/speak", json={"text": ""}, headers=dev)).status == 400


async def test_voice_unavailable_501(aiohttp_client, store, static_dir):
    client = await aiohttp_client(create_app(MockHost(speed=0), store, static_dir=static_dir))
    dev = await pair_device(client, store)
    resp = await client.post("/api/v1/voice/transcribe", data=b"abc", headers={**dev, "Content-Type": "audio/wav"})
    assert resp.status == 501 and (await resp.json())["error"]["code"] == "stt_unavailable"
    resp = await client.post("/api/v1/voice/speak", json={"text": "x"}, headers=dev)
    assert resp.status == 501 and (await resp.json())["error"]["code"] == "tts_unavailable"


# ---- signed assets -----------------------------------------------------

async def test_signed_asset_valid_tampered_expired(client, store):
    dev = await pair_device(client, store)
    url = (await (await client.post("/api/v1/voice/speak", json={"text": "hi"}, headers=dev)).json())["url"]
    assert (await client.get(url)).status == 200
    path, query = url.split("?")
    exp = int(query.split("exp=")[1].split("&")[0])
    tampered = f"{path}?exp={exp}&sig={'0' * 32}"
    assert (await client.get(tampered)).status == 401
    assert (await client.get(path + "?exp=abc&sig=x")).status == 401
    old = int(time.time()) - 10
    expired = f"{path}?exp={old}&sig={sign_asset(store.signing_secret, path, old)}"
    assert (await client.get(expired)).status == 401
    assert (await client.get(f"/api/v1/assets/audio/nope?exp={int(time.time()) + 60}&sig=00")).status == 401


async def test_avatar_asset_resize(aiohttp_client, store, static_dir, tmp_path):
    from PIL import Image

    from hermes_r1.bridge.host import AssetFile

    class ImageHost(MockHost):
        async def list_bots(self):
            bots = await super().list_bots()
            bots[1]["avatar"] = {"kind": "image", "shape": "circle", "color": "#fff"}
            return bots

        async def avatar_file(self, bot_id):
            if bot_id != "coder":
                return None
            path = tmp_path / "avatar.png"
            Image.new("RGB", (300, 120), (200, 30, 30)).save(path)
            return AssetFile(path=path, content_type="image/png")

    client = await aiohttp_client(create_app(ImageHost(speed=0), store, static_dir=static_dir))
    dev = await pair_device(client, store)
    bots = (await (await client.get("/api/v1/bootstrap", headers=dev)).json())["bots"]
    coder = next(b for b in bots if b["id"] == "coder")
    assert coder["avatar"]["url"].startswith("/api/v1/assets/avatar/coder?exp=")
    resp = await client.get(coder["avatar"]["url"] + "&s=48")
    assert resp.status == 200 and resp.headers["Content-Type"] == "image/png"
    assert Image.open(__import__("io").BytesIO(await resp.read())).size == (48, 48)
    resp = await client.get(coder["avatar"]["url"] + "&s=999")
    assert resp.status == 400


async def test_bot_overlay_and_avatar_overrides(client, store):
    dev = await pair_device(client, store)
    rev = (await (await client.put("/api/v1/admin/config", headers=admin(store), json={
        "avatar_overrides": {"coder": {"shape": "hexagon", "color": "#123456"}},
    })).json())["config_rev"]
    bots = (await (await client.get("/api/v1/bots", headers=dev)).json())
    assert bots["config_rev"] == rev
    coder = next(b for b in bots["bots"] if b["id"] == "coder")
    assert coder["avatar"]["shape"] == "hexagon" and coder["avatar"]["color"] == "#123456"


# ---- inbox -------------------------------------------------------------

async def test_inbox_file_and_admin_post(client, store):
    dev = await pair_device(client, store)
    store.inbox_path.write_text(json.dumps({"title": "Deploy done", "body": "ok", "level": "success"}) + "\n")
    data = await (await client.get("/api/v1/inbox?after=0&wait=0", headers=dev)).json()
    assert [i["title"] for i in data["items"]] == ["Deploy done"]
    assert data["next"] == data["items"][-1]["id"]
    again = await (await client.get(f"/api/v1/inbox?after={data['next']}&wait=0", headers=dev)).json()
    assert again == {"items": [], "next": data["next"]}
    resp = await client.post("/api/v1/admin/inbox", headers=admin(store),
                             json={"title": "Test", "body": "hello", "bot": "ops", "level": "warn"})
    assert (await resp.json())["item"]["level"] == "warn"


async def test_inbox_long_poll_returns_early(client, store):
    dev = await pair_device(client, store)
    cursor = (await (await client.get("/api/v1/inbox", headers=dev)).json())["next"]

    async def later():
        await asyncio.sleep(0.3)
        await client.post("/api/v1/admin/inbox", headers=admin(store), json={"title": "late"})

    started = time.monotonic()
    poll = asyncio.create_task(client.get(f"/api/v1/inbox?after={cursor}&wait=10", headers=dev))
    await later()
    data = await (await poll).json()
    assert data["items"][0]["title"] == "late"
    assert time.monotonic() - started < 5


# ---- admin -------------------------------------------------------------

async def test_admin_requires_token(client):
    assert (await client.get("/api/v1/admin/state")).status == 401
    assert (await client.get("/api/v1/admin/state", headers={"Authorization": "Bearer x"})).status == 401


async def test_admin_forbidden_with_proxy_header(client, store):
    for header in ("X-Forwarded-For", "CF-Connecting-IP", "Tailscale-User-Login"):
        resp = await client.get("/api/v1/admin/state", headers={**admin(store), header: "1.2.3.4"})
        assert resp.status == 403
        assert (await resp.json())["error"]["code"] == "admin_local_only"


async def test_admin_state_shape(client, store):
    dev = await pair_device(client, store)
    data = await (await client.get("/api/v1/admin/state", headers=admin(store))).json()
    assert {"config", "config_rev", "devices", "bots", "public_url", "local_url", "voice", "mock", "version"} <= set(data)
    assert [d["name"] for d in data["devices"]] == ["test r1"]
    assert "token_hash" not in data["devices"][0]
    assert data["local_url"] == ""
    assert dev


async def test_config_round_trip_and_normalization(client, store):
    rev0 = store.config_rev
    resp = await client.put("/api/v1/admin/config", headers=admin(store), json={
        "font_scale": 99, "theme": {"preset": "ember", "overrides": {"bg": "red;}"}}, "evil": True,
    })
    assert resp.status == 200
    data = await resp.json()
    assert data["config_rev"] == rev0 + 1
    assert data["config"]["font_scale"] == 1.3
    assert data["config"]["theme"] == {"preset": "ember", "overrides": {}}
    assert "evil" not in data["config"]
    bad = await client.put("/api/v1/admin/config", headers=admin(store), json=[1, 2])
    assert bad.status == 400 and (await bad.json())["error"]["code"] == "bad_config"


async def test_admin_pair_returns_qr_with_public_url(client, store):
    resp = await client.put("/api/v1/admin/public-url", headers=admin(store), json={"url": "https://r1.example.com/"})
    assert (await resp.json()) == {"public_url": "https://r1.example.com"}
    data = await (await client.post("/api/v1/admin/pair", headers=admin(store))).json()
    assert data["url"] == f"https://r1.example.com/?pair={data['code']}"
    assert data["qr"]["url"] == data["url"]
    assert data["qr"]["iconUrl"] == "https://r1.example.com/nous-girl.png"
    assert data["expires"] > time.time()
    bad = await client.put("/api/v1/admin/public-url", headers=admin(store), json={"url": "ftp://x"})
    assert bad.status == 400


async def test_preview_token_works_but_is_not_listed(client, store):
    token = (await (await client.post("/api/v1/admin/preview-token", headers=admin(store))).json())["token"]
    resp = await client.get("/api/v1/bootstrap", headers={"Authorization": f"Bearer {token}"})
    assert resp.status == 200
    state = await (await client.get("/api/v1/admin/state", headers=admin(store))).json()
    assert state["devices"] == []


async def test_delete_device_revokes_token(client, store):
    dev = await pair_device(client, store)
    state = await (await client.get("/api/v1/admin/state", headers=admin(store))).json()
    device_id = state["devices"][0]["id"]
    resp = await client.delete(f"/api/v1/admin/devices/{device_id}", headers=admin(store))
    assert (await resp.json()) == {"ok": True}
    assert (await client.get("/api/v1/bootstrap", headers=dev)).status == 401
    assert (await client.delete(f"/api/v1/admin/devices/{device_id}", headers=admin(store))).status == 404


async def test_admin_inbox_requires_title(client, store):
    resp = await client.post("/api/v1/admin/inbox", headers=admin(store), json={"body": "x"})
    assert resp.status == 400


# ---- static apps and headers -------------------------------------------

async def test_root_serves_app_with_security_headers(client):
    resp = await client.get("/")
    assert resp.status == 200
    assert "<title>r1</title>" in await resp.text()
    assert resp.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["Cache-Control"] == "no-cache"
    assert (await client.get("/app.js")).status == 200
    assert (await client.get("/../../etc/passwd")).status == 404


async def test_api_responses_are_no_store(client, store):
    resp = await client.get("/api/v1/health")
    assert resp.headers["Cache-Control"] == "no-store"
    dev = await pair_device(client, store)
    resp = await client.get("/api/v1/unknown", headers=dev)
    assert resp.status == 404 and (await resp.json())["error"]["code"] == "not_found"


async def test_studio_not_built_and_redirect(client):
    resp = await client.get("/studio/")
    assert resp.status == 200 and "Not built yet" in await resp.text()
    resp = await client.get("/studio", allow_redirects=False)
    assert resp.status == 302 and resp.headers["Location"] == "/studio/"


async def test_path_mounted_bridge_proxy_strips_prefix(aiohttp_client, store, static_dir):
    (static_dir / "app" / "index.html").write_text(
        '<!doctype html><head><meta name="r1-api-base" content="__R1_API_BASE__"></head>'
    )
    app = create_app(MockHost(speed=0), store, static_dir=static_dir, base_path="/r1")
    client = await aiohttp_client(app)

    funnel = {"Tailscale-Funnel-Request": "1"}
    page = await client.get("/", headers=funnel)
    html = await page.text()
    assert page.status == 200
    assert '<base href="/r1/">' in html
    assert 'name="r1-api-base" content="/r1"' in html
    assert (await client.get("/app.js", headers=funnel)).status == 200
    assert (await client.get("/api/v1/health", headers=funnel)).status == 200
    assert (await client.get("/api/v1/bots", headers=funnel)).status == 401
    local_page = await client.get("/")
    assert '<base href="./">' in await local_page.text()
