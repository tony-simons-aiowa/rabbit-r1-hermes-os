"""Composite Host for a local Hermes install and explicitly configured gateways."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from .hermes_host import HermesHost
from .host import AssetFile, Host, HostError, Json, TurnHandle

_ID = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")


def _read_key(key_env: object) -> str:
    if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError("key_env must be an environment variable name")
    key = os.environ.get(key_env)
    if not key:
        raise ValueError(f"environment variable {key_env} is missing or empty")
    return key


def load_gateways(path: Path) -> list[Json]:
    """Load and validate gateway config. Never include URLs or credentials in errors."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("could not read gateways JSON file") from exc
    if not isinstance(raw, list):
        raise ValueError("gateways file must contain a JSON array")
    result: list[Json] = []
    ids: set[str] = {"local"}
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"id", "name", "url", "key_env", "profiles"}:
            raise ValueError("each gateway requires id, name, url, and key_env, with optional profiles")
        gid, name, url, key_env = (item.get(k) for k in ("id", "name", "url", "key_env"))
        if not isinstance(gid, str) or not _ID.fullmatch(gid) or gid in ids:
            raise ValueError("gateway ids must be unique safe identifiers")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("gateway name must be a non-empty string of at most 80 characters")
        if not isinstance(url, str):
            raise ValueError("gateway URL must be a string")
        _validate_url(url)
        profiles = item.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ValueError("profiles must map profile IDs to name and key_env")
        resolved = {}
        for profile_id, profile in profiles.items():
            if not _ID.fullmatch(profile_id) or profile_id == "default":
                raise ValueError("invalid named profile ID")
            if not isinstance(profile, dict) or set(profile) != {"name", "key_env"}:
                raise ValueError("each named profile requires name and key_env")
            label = profile["name"]
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                raise ValueError("profile name must be a non-empty string of at most 80 characters")
            resolved[profile_id] = {"name": label.strip(), "key": _read_key(profile["key_env"])}
        ids.add(gid)
        result.append({"id": gid, "name": name.strip(), "url": url.rstrip("/"),
                       "key": _read_key(key_env), "profiles": resolved})
    return result


def _validate_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        _ = parts.port  # urlsplit checks port validity when the property is read.
        host = (parts.hostname or "").lower()
        safe_http = False
        if host == "localhost":
            safe_http = True
        else:
            try:
                safe_http = ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        if (parts.scheme != "https" and not (parts.scheme == "http" and safe_http)):
            raise ValueError
        if not host or parts.username is not None or "?" in url or "#" in url:
            raise ValueError
        if any(ch.isspace() for ch in url):
            raise ValueError
    except ValueError as exc:
        raise ValueError("gateway URL must use HTTPS, or HTTP on a loopback address") from exc


class _RemoteHost(HermesHost):
    """HermesHost exposing the default and explicitly configured named profiles."""

    def __init__(self, hermes_home: Path, gateway: Json) -> None:
        super().__init__(hermes_home, gateway["url"])
        self._profiles = {
            "default": {"name": gateway["name"], "key": gateway["key"]},
            **gateway.get("profiles", {}),
        }

    def _upstream(self, bot_id: str) -> tuple[str, str]:
        profile = self._profiles.get(bot_id)
        if profile is None:
            raise HostError("not_found", "Unknown bot.", 404)
        base = self._api_url if bot_id == "default" else f"{self._api_url}/p/{bot_id}"
        return base, profile["key"]

    def _discover(self) -> list[Json]:
        return [{
            "id": profile_id, "name": profile["name"], "title": "", "description": "",
            "model": "", "is_default": profile_id == "default", "reachable": True, "locked_reason": "",
            "avatar": {"kind": "shape", "shape": "circle", "color": "", "url": ""},
            "pet": None, "state": "idle", "active": False, "last": None,
        } for profile_id, profile in self._profiles.items()]

    async def avatar_file(self, bot_id: str) -> AssetFile | None:
        return None

    async def pet_file(self, bot_id: str) -> AssetFile | None:
        return None

    def voice_capabilities(self) -> Json:
        return {"stt": False, "tts": False}

    async def transcribe(self, audio_path: Path, content_type: str) -> str:
        raise HostError("stt_unavailable", "Speech to text is unavailable.", 501)

    async def synthesize(self, text: str, out_dir: Path) -> AssetFile:
        raise HostError("tts_unavailable", "Text to speech is unavailable.", 501)


class GatewayHost:
    """Composite host; every external bot ID has exactly one owning upstream."""
    mock = False

    def __init__(self, primary: Host, hermes_home: Path, gateways: list[Json]) -> None:
        self.primary = primary
        self.remote = {g["id"]: (_RemoteHost(hermes_home, g), g["name"]) for g in gateways}

    async def fleet_gateways(self) -> list[Json]:
        """Probe local and configured gateways concurrently, returning safe status."""
        specs: list[tuple[str, str, Host]] = [("local", "Local", self.primary)]
        specs.extend((gid, name, host) for gid, (host, name) in self.remote.items())
        async def probe(spec: tuple[str, str, Host]) -> Json:
            gid, name, host = spec
            if getattr(host, "mock", False):
                return {"id": gid, "name": name, "status": "online"}
            probe_method = getattr(host, "fleet_probe", None)
            if not callable(probe_method):
                return {"id": gid, "name": name, "status": "unknown"}
            try:
                status = await probe_method()
            except asyncio.CancelledError:
                raise
            except Exception:
                status = "offline"
            return {"id": gid, "name": name, "status": status}
        results = await asyncio.gather(*(probe(spec) for spec in specs), return_exceptions=True)
        safe: list[Json] = []
        for spec, result in zip(specs, results):
            if isinstance(result, BaseException):
                safe.append({"id": spec[0], "name": spec[1], "status": "unknown"})
            else:
                safe.append(result)
        return safe

    async def start(self) -> None:
        await self.primary.start()
        for host, _ in self.remote.values():
            await host.start()

    async def close(self) -> None:
        for host, _ in self.remote.values():
            await host.close()
        await self.primary.close()

    def _route(self, bot_id: str) -> tuple[Host, str]:
        if ":" in bot_id:
            gateway_id, profile = bot_id.split(":", 1)
            entry = self.remote.get(gateway_id)
            if entry is not None:
                return entry[0], profile
            raise HostError("not_found", "Unknown bot.", 404)
        return self.primary, bot_id

    async def list_bots(self) -> list[Json]:
        bots = await self.primary.list_bots()
        for bot in bots:
            bot.update(gateway_id="local", gateway_name="Local")
        for gid, (host, name) in self.remote.items():
            for bot in await host.list_bots():
                bot["id"] = f"{gid}:{bot['id']}"
                bot.update(gateway_id=gid, gateway_name=name)
                bots.append(bot)
        return bots

    async def avatar_file(self, bot_id: str) -> AssetFile | None:
        host, local = self._route(bot_id)
        return await host.avatar_file(local)

    async def pet_file(self, bot_id: str) -> AssetFile | None:
        host, local = self._route(bot_id)
        return await host.pet_file(local)

    async def list_sessions(self, bot_id: str, limit: int) -> list[Json]:
        host, local = self._route(bot_id)
        return await host.list_sessions(local, limit)

    async def memory_graph(self, bot_id: str) -> Json:
        graph = getattr(self.primary, "memory_graph", None)
        if ":" in bot_id or graph is None:
            raise HostError("graph_unavailable", "Memory Graph is available for profiles on the bridge host.", 501)
        return await graph(bot_id)

    async def get_messages(self, bot_id: str, session_id: str, limit: int) -> list[Json]:
        host, local = self._route(bot_id)
        return await host.get_messages(local, session_id, limit)

    async def run_turn(self, bot_id: str, session_id: str | None, text: str) -> TurnHandle:
        host, local = self._route(bot_id)
        return await host.run_turn(local, session_id, text)

    async def stop_turn(self, bot_id: str, session_id: str) -> None:
        host, local = self._route(bot_id)
        await host.stop_turn(local, session_id)

    async def respond(self, bot_id: str, session_id: str, request_id: str, *, choice: str | None = None,
                      answer: str | None = None) -> None:
        host, local = self._route(bot_id)
        await host.respond(local, session_id, request_id, choice=choice, answer=answer)

    def voice_capabilities(self) -> Json:
        return self.primary.voice_capabilities()

    async def transcribe(self, audio_path: Path, content_type: str) -> str:
        return await self.primary.transcribe(audio_path, content_type)

    async def synthesize(self, text: str, out_dir: Path) -> AssetFile:
        return await self.primary.synthesize(text, out_dir)
