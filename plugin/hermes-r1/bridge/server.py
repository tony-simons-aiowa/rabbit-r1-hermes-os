"""aiohttp application: SPEC 3.3 device routes, 3.4 admin routes, assets and static apps."""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import os
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from aiohttp import web
from PIL import Image, ImageOps

from .auth import RateLimiter, is_local_request, signed_url, verify_asset
from .host import AssetFile, Host, HostError, Json
from .store import Store
from .turns import MAX_WAIT, TurnManager

log = logging.getLogger(__name__)

MAX_JSON_BYTES = 64 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_TTS_TEXT = 2000
MAX_TURN_TEXT = 8000
MAX_ANSWER_TEXT = 4000
AVATAR_MIN, AVATAR_MAX = 16, 256
AVATAR_CACHE_SIZE = 64
ASSET_CACHE_CONTROL = "private, max-age=3600"
CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; connect-src 'self'"
)
PUBLIC_API = frozenset({"/api/v1/health", "/api/v1/pair"})
NOT_BUILT_HTML = (
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
    "<title>Not built yet</title><body style='font:16px system-ui;padding:24px'>"
    "<p>Not built yet — run <code>npm run build</code>.</p>"
)

REQ_DEVICE = web.RequestKey("device", dict)
APP_HOST = web.AppKey("host", object)
APP_STORE = web.AppKey("store", Store)
APP_TURNS = web.AppKey("turns", TurnManager)
APP_LIMITER = web.AppKey("limiter", RateLimiter)
APP_STATIC = web.AppKey("static_dir", Path)
APP_ALLOW_REMOTE = web.AppKey("allow_remote_admin", bool)
APP_VERSION = web.AppKey("version", str)
APP_LOCAL_URL = web.AppKey("local_url", str)
APP_AVATAR_CACHE = web.AppKey("avatar_cache", OrderedDict)


# ---- errors and headers ------------------------------------------------

def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 413: "too_large"}


@web.middleware
async def error_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    api = request.path.startswith("/api/")
    try:
        response = await handler(request)
    except HostError as exc:
        response = _json_error(exc.status, exc.code, exc.message)
    except web.HTTPException as exc:
        if not api:
            raise
        response = _json_error(exc.status, _HTTP_CODES.get(exc.status, "http_error"),
                               exc.reason or "Request failed.")
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("unhandled error on %s %s", request.method, request.path)
        response = _json_error(500, "internal", "Internal server error.")
    if api:
        response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


# ---- auth --------------------------------------------------------------

def _bearer(request: web.Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip() or None
    return None


def _require_device(request: web.Request) -> Json:
    device = request.app[APP_STORE].device_for_token(_bearer(request))
    if device is None:
        raise HostError("unauthorized", "Pair this device first.", 401)
    return device


def _require_admin(request: web.Request) -> None:
    if not request.app[APP_ALLOW_REMOTE] and not is_local_request(request):
        raise HostError("admin_local_only", "Studio admin is only available from this computer.", 403)
    token = _bearer(request)
    if not token or not hmac.compare_digest(token, request.app[APP_STORE].admin_token):
        raise HostError("unauthorized", "Admin token required.", 401)


@web.middleware
async def auth_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    path = request.path
    if path.startswith("/api/v1/admin/"):
        _require_admin(request)
    elif path.startswith("/api/v1/") and path not in PUBLIC_API and not path.startswith("/api/v1/assets/"):
        request[REQ_DEVICE] = _require_device(request)
    return await handler(request)


# ---- request helpers ---------------------------------------------------

async def _json_body(request: web.Request, error_code: str = "bad_json") -> Json:
    """Read a JSON object body of at most 64 KB; an empty body means {}."""
    if request.content_length and request.content_length > MAX_JSON_BYTES:
        raise HostError("too_large", "Request body is too large.", 413)
    raw = await request.read()
    if len(raw) > MAX_JSON_BYTES:
        raise HostError("too_large", "Request body is too large.", 413)
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise HostError(error_code, "Request body is not valid JSON.", 400)
    if not isinstance(body, dict):
        raise HostError(error_code, "Request body must be a JSON object.", 400)
    return body


def _int_query(request: web.Request, name: str, default: int, lo: int, hi: int) -> int:
    raw = request.query.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HostError("bad_request", f"{name} must be an integer.", 400)
    return max(lo, min(hi, value))


def _float_query(request: web.Request, name: str, default: float, lo: float, hi: float) -> float:
    raw = request.query.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise HostError("bad_request", f"{name} must be a number.", 400)
    if value != value:  # NaN
        raise HostError("bad_request", f"{name} must be a number.", 400)
    return max(lo, min(hi, value))


def _required_str(body: Json, key: str, *, max_len: int, message: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise HostError("bad_request", message, 400)
    return value


def _client_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip()
    return request.remote or "unknown"


def _file_response(asset: AssetFile) -> web.FileResponse:
    if not asset.path.is_file():
        raise HostError("not_found", "Asset not found.", 404)
    response = web.FileResponse(asset.path, headers={"Cache-Control": ASSET_CACHE_CONTROL})
    response.content_type = asset.content_type
    return response


# ---- bots --------------------------------------------------------------

def _newer_last(host_last: Json | None, mine: Json | None) -> Json | None:
    if mine is None:
        return host_last
    if host_last is None or float(mine["ts"]) > float(host_last.get("ts", 0)):
        return mine
    return host_last


def _avatar_view(bot: Json, override: Json | None, secret: str) -> Json:
    bot_id = bot["id"]
    avatar = dict(bot.get("avatar") or {})
    avatar.setdefault("kind", "shape")
    avatar.setdefault("shape", "circle")
    avatar.setdefault("color", "")
    if override:
        avatar.update({k: v for k, v in override.items() if k in ("shape", "color")})
    avatar["url"] = (
        signed_url(secret, f"/api/v1/assets/avatar/{bot_id}") if avatar["kind"] == "image" else ""
    )
    return avatar


def _pet_view(bot_id: str, pet: Json | None, secret: str) -> Json | None:
    if pet is None:
        return None
    return {**pet, "url": signed_url(secret, f"/api/v1/assets/pet/{bot_id}")}


async def _bots_view(app: web.Application) -> list[Json]:
    host: Any = app[APP_HOST]
    store: Store = app[APP_STORE]
    turns: TurnManager = app[APP_TURNS]
    overrides = store.get_config()["avatar_overrides"]
    secret = store.signing_secret
    out: list[Json] = []
    for raw in await host.list_bots():
        bot = dict(raw)
        bot_id = str(bot["id"])
        overlay = turns.overlay(bot_id)
        bot["state"] = overlay["state"]
        bot["active"] = overlay["active"]
        bot["last"] = _newer_last(bot.get("last"), overlay["last"])
        bot["avatar"] = _avatar_view(bot, overrides.get(bot_id), secret)
        bot["pet"] = _pet_view(bot_id, bot.get("pet"), secret)
        out.append(bot)
    return out


# ---- device routes -----------------------------------------------------

async def health(request: web.Request) -> web.Response:
    app = request.app
    return web.json_response({
        "ok": True,
        "name": "hermes-r1",
        "version": app[APP_VERSION],
        "mock": bool(app[APP_HOST].mock),
    })


async def pair(request: web.Request) -> web.Response:
    app = request.app
    limiter: RateLimiter = app[APP_LIMITER]
    key = f"pair:{_client_ip(request)}"
    if limiter.is_limited(key):
        raise HostError("rate_limited", "Too many failed attempts. Try again later.", 429)
    body = await _json_body(request)
    code = body.get("code") if isinstance(body.get("code"), str) else ""
    name = body.get("name") if isinstance(body.get("name"), str) else ""
    name = name.strip()[:40] or "rabbit r1"
    result = app[APP_STORE].redeem_pair_code(code, name)
    if result is None:
        limiter.record_failure(key)
        raise HostError("bad_code", "That pairing code is invalid or expired.", 400)
    token, device = result
    return web.json_response({"token": token, "device": {"id": device["id"], "name": device["name"]}})


async def bootstrap(request: web.Request) -> web.Response:
    app = request.app
    device = request[REQ_DEVICE]
    host: Any = app[APP_HOST]
    store: Store = app[APP_STORE]
    caps = host.voice_capabilities()
    return web.json_response({
        "server": {"version": app[APP_VERSION], "mock": bool(host.mock)},
        "device": {"id": device["id"], "name": device["name"]},
        "config": store.get_config(),
        "config_rev": store.config_rev,
        "bots": await _bots_view(app),
        "voice": {"stt": bool(caps.get("stt")), "tts": bool(caps.get("tts"))},
    })


async def list_bots(request: web.Request) -> web.Response:
    store: Store = request.app[APP_STORE]
    return web.json_response({"bots": await _bots_view(request.app), "config_rev": store.config_rev})


async def list_sessions(request: web.Request) -> web.Response:
    host: Any = request.app[APP_HOST]
    limit = _int_query(request, "limit", 20, 1, 100)
    sessions = await host.list_sessions(request.match_info["bot"], limit)
    return web.json_response({"sessions": sessions})


async def list_messages(request: web.Request) -> web.Response:
    host: Any = request.app[APP_HOST]
    limit = _int_query(request, "limit", 30, 1, 200)
    messages = await host.get_messages(request.match_info["bot"], request.match_info["sid"], limit)
    return web.json_response({"messages": messages})


async def start_turn(request: web.Request) -> web.Response:
    turns: TurnManager = request.app[APP_TURNS]
    body = await _json_body(request)
    text = _required_str(body, "text", max_len=MAX_TURN_TEXT, message="text is required.")
    session_id = body.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise HostError("bad_request", "session_id must be a string or null.", 400)
    speak = body.get("speak") is True
    turn = await turns.start(request[REQ_DEVICE]["id"], request.match_info["bot"],
                             session_id or None, text.strip(), speak)
    return web.json_response({"turn_id": turn.id, "session_id": turn.session_id})


async def turn_events(request: web.Request) -> web.Response:
    turns: TurnManager = request.app[APP_TURNS]
    after = _int_query(request, "after", 0, 0, 2**53)
    wait = _float_query(request, "wait", 20.0, 0.0, MAX_WAIT)
    events, nxt, done = await turns.wait_events(
        request.match_info["tid"], after, wait, device_id=request[REQ_DEVICE]["id"])
    return web.json_response({"events": events, "next": nxt, "done": done})


async def stop_turn(request: web.Request) -> web.Response:
    turns: TurnManager = request.app[APP_TURNS]
    await turns.stop(request.match_info["tid"], device_id=request[REQ_DEVICE]["id"])
    return web.json_response({"ok": True})


async def respond_turn(request: web.Request) -> web.Response:
    turns: TurnManager = request.app[APP_TURNS]
    body = await _json_body(request)
    request_id = _required_str(body, "request_id", max_len=100, message="request_id is required.")
    choice = body.get("choice")
    answer = body.get("answer")
    if choice is None and answer is None:
        raise HostError("bad_request", "choice or answer is required.", 400)
    if choice is not None and (not isinstance(choice, str) or len(choice) > 64):
        raise HostError("bad_request", "choice must be a short string.", 400)
    if answer is not None and not isinstance(answer, str):
        raise HostError("bad_request", "answer must be a string.", 400)
    await turns.respond(request.match_info["tid"], request_id, choice,
                        answer[:MAX_ANSWER_TEXT] if answer is not None else None,
                        device_id=request[REQ_DEVICE]["id"])
    return web.json_response({"ok": True})


def _audio_suffix(content_type: str) -> str:
    ct = content_type.lower()
    if "webm" in ct:
        return ".webm"
    if "ogg" in ct:
        return ".ogg"
    if "m4a" in ct:
        return ".m4a"
    if "mp4" in ct:
        return ".mp4"
    if "wav" in ct:
        return ".wav"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    return ".webm"


async def _read_audio(request: web.Request) -> bytes:
    if request.content_length and request.content_length > MAX_AUDIO_BYTES:
        raise HostError("too_large", "Audio is too large.", 413)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > MAX_AUDIO_BYTES:
            raise HostError("too_large", "Audio is too large.", 413)
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HostError("bad_request", "Audio body is empty.", 400)
    return data


async def transcribe(request: web.Request) -> web.Response:
    host: Any = request.app[APP_HOST]
    content_type = request.content_type or "application/octet-stream"
    data = await _read_audio(request)
    fd, tmp_name = tempfile.mkstemp(suffix=_audio_suffix(content_type), prefix="r1-voice-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        text = await host.transcribe(tmp, content_type)
    finally:
        tmp.unlink(missing_ok=True)
    return web.json_response({"text": text})


async def speak(request: web.Request) -> web.Response:
    app = request.app
    host: Any = app[APP_HOST]
    turns: TurnManager = app[APP_TURNS]
    body = await _json_body(request)
    text = _required_str(body, "text", max_len=MAX_TTS_TEXT, message="text is required.")
    if not host.voice_capabilities().get("tts"):
        raise HostError("tts_unavailable", "Text to speech is not available.", 501)
    asset = await host.synthesize(text, turns.audio_dir)
    audio_id = turns.register_audio(asset)
    return web.json_response({"url": turns.audio_url(audio_id), "mime": asset.content_type})


async def inbox(request: web.Request) -> web.Response:
    store: Store = request.app[APP_STORE]
    after = _int_query(request, "after", 0, 0, 2**53)
    wait = _float_query(request, "wait", 0.0, 0.0, 25.0)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    store.ingest_inbox_file()
    items = store.inbox_after(after)
    while not items and loop.time() < deadline:
        await asyncio.sleep(min(1.0, max(0.0, deadline - loop.time())))
        store.ingest_inbox_file()
        items = store.inbox_after(after)
    nxt = items[-1]["id"] if items else after
    return web.json_response({"items": items, "next": nxt})


# ---- assets ------------------------------------------------------------

def _render_avatar(path: Path, size: int) -> bytes:
    with Image.open(path) as img:
        fitted = ImageOps.fit(img.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    fitted.save(buffer, format="PNG")
    return buffer.getvalue()


async def _avatar_png(app: web.Application, asset: AssetFile, size: int) -> bytes:
    mtime = asset.path.stat().st_mtime_ns
    key = (str(asset.path), mtime, size)
    cache: OrderedDict[tuple[str, int, int], bytes] = app[APP_AVATAR_CACHE]
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    try:
        body = await asyncio.to_thread(_render_avatar, asset.path, size)
    except (OSError, ValueError) as exc:
        raise HostError("not_found", f"Avatar could not be read: {exc}", 404)
    cache[key] = body
    while len(cache) > AVATAR_CACHE_SIZE:
        cache.popitem(last=False)
    return body


async def _avatar_response(request: web.Request, bot_id: str) -> web.StreamResponse:
    host: Any = request.app[APP_HOST]
    found = await host.avatar_file(bot_id)
    if found is None:
        raise HostError("not_found", "No avatar for this bot.", 404)
    if "s" not in request.query:
        return _file_response(found)
    try:
        size = int(request.query["s"])
    except ValueError:
        raise HostError("bad_request", "s must be an integer.", 400)
    if not AVATAR_MIN <= size <= AVATAR_MAX:
        raise HostError("bad_request", "s must be between 16 and 256.", 400)
    body = await _avatar_png(request.app, found, size)
    return web.Response(body=body, content_type="image/png",
                        headers={"Cache-Control": ASSET_CACHE_CONTROL})


async def asset(request: web.Request) -> web.StreamResponse:
    app = request.app
    store: Store = app[APP_STORE]
    try:
        exp = int(request.query.get("exp", ""))
    except ValueError:
        raise HostError("unauthorized", "Asset link is invalid.", 401)
    if not verify_asset(store.signing_secret, request.path, exp, request.query.get("sig", "")):
        raise HostError("unauthorized", "Asset link is invalid or expired.", 401)
    kind = request.match_info["kind"]
    name = request.match_info["name"]
    if kind == "avatar":
        return await _avatar_response(request, name)
    if kind == "pet":
        found = await app[APP_HOST].pet_file(name)
    elif kind == "audio":
        found = app[APP_TURNS].audio_file(name)
    else:
        raise HostError("not_found", "Unknown asset kind.", 404)
    if found is None:
        raise HostError("not_found", "Asset not found.", 404)
    return _file_response(found)


# ---- admin routes ------------------------------------------------------

async def admin_state(request: web.Request) -> web.Response:
    app = request.app
    store: Store = app[APP_STORE]
    host: Any = app[APP_HOST]
    caps = host.voice_capabilities()
    devices = [
        {"id": d["id"], "name": d["name"], "created": d["created"], "last_seen": d["last_seen"]}
        for d in store.list_devices()
    ]
    return web.json_response({
        "config": store.get_config(),
        "config_rev": store.config_rev,
        "devices": devices,
        "bots": await _bots_view(app),
        "public_url": store.public_url,
        "local_url": app[APP_LOCAL_URL],
        "voice": {"stt": bool(caps.get("stt")), "tts": bool(caps.get("tts"))},
        "mock": bool(host.mock),
        "version": app[APP_VERSION],
    })


async def admin_put_config(request: web.Request) -> web.Response:
    store: Store = request.app[APP_STORE]
    body = await _json_body(request, error_code="bad_config")
    try:
        config, rev = store.set_config(body)
    except ValueError as exc:
        raise HostError("bad_config", str(exc), 400)
    return web.json_response({"config": config, "config_rev": rev})


async def admin_pair(request: web.Request) -> web.Response:
    store: Store = request.app[APP_STORE]
    code, expires = store.create_pair_code()
    public = store.public_url
    url = f"{public}/?pair={code}"
    return web.json_response({
        "code": code,
        "expires": expires,
        "url": url,
        "qr": {
            "title": "Hermes",
            "url": url,
            "description": "Hermes Agent for rabbit r1",
            "iconUrl": f"{public}/icon.png",
            "themeColor": "#4a84fe",
        },
    })


async def admin_delete_device(request: web.Request) -> web.Response:
    store: Store = request.app[APP_STORE]
    if not store.remove_device(request.match_info["id"]):
        raise HostError("not_found", "No such device.", 404)
    return web.json_response({"ok": True})


async def admin_preview_token(request: web.Request) -> web.Response:
    return web.json_response({"token": request.app[APP_STORE].create_preview_token()})


async def admin_public_url(request: web.Request) -> web.Response:
    store: Store = request.app[APP_STORE]
    body = await _json_body(request)
    url = body.get("url")
    if not isinstance(url, str):
        raise HostError("bad_url", "url must be a string.", 400)
    try:
        public = store.set_public_url(url)
    except ValueError as exc:
        raise HostError("bad_url", str(exc), 400)
    return web.json_response({"public_url": public})


async def admin_inbox(request: web.Request) -> web.Response:
    store: Store = request.app[APP_STORE]
    body = await _json_body(request)
    title = _required_str(body, "title", max_len=200, message="title is required.")
    text = body.get("body") if isinstance(body.get("body"), str) else ""
    bot = body.get("bot") if isinstance(body.get("bot"), str) else ""
    level = body.get("level") if isinstance(body.get("level"), str) else "info"
    item = store.add_inbox(title.strip(), text[:2000], bot[:100], level)
    return web.json_response({"item": item})


# ---- static apps -------------------------------------------------------

def _static_response(root: Path, rel: str) -> web.StreamResponse:
    if not (root / "index.html").is_file():
        return web.Response(text=NOT_BUILT_HTML, content_type="text/html",
                            headers={"Cache-Control": "no-cache"})
    rel = rel or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    base = root.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise web.HTTPNotFound()
    response = web.FileResponse(target)
    if target.suffix == ".html":
        response.headers["Cache-Control"] = "no-cache"
    return response


async def serve_app(request: web.Request) -> web.StreamResponse:
    rel = request.match_info.get("path", "")
    if rel == "api" or rel.startswith("api/"):
        raise HostError("not_found", "Unknown API route.", 404)
    return _static_response(request.app[APP_STATIC] / "app", rel)


async def serve_studio(request: web.Request) -> web.StreamResponse:
    return _static_response(request.app[APP_STATIC] / "studio", request.match_info.get("path", ""))


async def studio_redirect(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/studio/")


# ---- lifecycle and factory --------------------------------------------

async def _on_startup(app: web.Application) -> None:
    await app[APP_HOST].start()


async def _on_cleanup(app: web.Application) -> None:
    await app[APP_TURNS].close()
    await app[APP_HOST].close()


def _add_routes(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/v1/health", health)
    r.add_post("/api/v1/pair", pair)
    r.add_get("/api/v1/bootstrap", bootstrap)
    r.add_get("/api/v1/bots", list_bots)
    r.add_get("/api/v1/bots/{bot}/sessions", list_sessions)
    r.add_get("/api/v1/bots/{bot}/sessions/{sid}/messages", list_messages)
    r.add_post("/api/v1/bots/{bot}/turns", start_turn)
    r.add_get("/api/v1/turns/{tid}/events", turn_events)
    r.add_post("/api/v1/turns/{tid}/stop", stop_turn)
    r.add_post("/api/v1/turns/{tid}/respond", respond_turn)
    r.add_post("/api/v1/voice/transcribe", transcribe)
    r.add_post("/api/v1/voice/speak", speak)
    r.add_get("/api/v1/inbox", inbox)
    r.add_get("/api/v1/assets/{kind}/{name}", asset)
    r.add_get("/api/v1/admin/state", admin_state)
    r.add_put("/api/v1/admin/config", admin_put_config)
    r.add_post("/api/v1/admin/pair", admin_pair)
    r.add_delete("/api/v1/admin/devices/{id}", admin_delete_device)
    r.add_post("/api/v1/admin/preview-token", admin_preview_token)
    r.add_put("/api/v1/admin/public-url", admin_public_url)
    r.add_post("/api/v1/admin/inbox", admin_inbox)
    r.add_get("/studio", studio_redirect)
    r.add_get("/studio/", serve_studio)
    r.add_get("/studio/{path:.*}", serve_studio)
    r.add_get("/", serve_app)
    r.add_get("/{path:.*}", serve_app)


def create_app(
    host: Host,
    store: Store,
    *,
    static_dir: Path,
    allow_remote_admin: bool = False,
    version: str = "0.1.0",
) -> web.Application:
    """Build the bridge application. ``host`` and ``store`` are owned by the caller."""
    app = web.Application(
        middlewares=[error_middleware, auth_middleware],
        client_max_size=MAX_AUDIO_BYTES,
    )
    app[APP_HOST] = host
    app[APP_STORE] = store
    app[APP_TURNS] = TurnManager(host, store, store.state_dir / "audio")
    app[APP_LIMITER] = RateLimiter()
    app[APP_STATIC] = Path(static_dir)
    app[APP_ALLOW_REMOTE] = allow_remote_admin
    app[APP_VERSION] = version
    app[APP_LOCAL_URL] = ""
    app[APP_AVATAR_CACHE] = OrderedDict()
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    _add_routes(app)
    return app
