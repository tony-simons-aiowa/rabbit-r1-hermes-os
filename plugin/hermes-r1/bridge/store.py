"""Persistent bridge state: tokens, devices, config, pairing codes and inbox.

State lives in ``state_dir/state.json`` (atomic writes, mode 0600). Inbox lines
written by other processes (the ``r1_notify`` tool) are tailed from
``state_dir/inbox.jsonl``. All methods are synchronous and cheap.
"""

from __future__ import annotations

import copy
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .auth import hash_token
from .config import DEFAULT_CONFIG, normalize_config

PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIR_TTL = 600
PREVIEW_TTL = 12 * 3600
LAST_SEEN_INTERVAL = 60
INBOX_KEEP = 100
INBOX_READ_CHUNK = 1024 * 1024
INBOX_LEVELS = ("info", "success", "warn")


def default_state_dir() -> Path:
    """``$HERMES_R1_HOME``, else ``$HERMES_HOME/r1``, else ``~/.hermes/r1``."""
    explicit = os.environ.get("HERMES_R1_HOME")
    if explicit:
        return Path(explicit).expanduser()
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        return Path(hermes_home).expanduser() / "r1"
    return Path.home() / ".hermes" / "r1"


def _public_device(device: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in device.items() if k != "token_hash"}


class Store:
    """JSON-backed state for one bridge instance."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        self.path = self.state_dir / "state.json"
        self.inbox_path = self.state_dir / "inbox.jsonl"
        self._lock = threading.RLock()
        self._pair_codes: dict[str, float] = {}
        self._data = self._load()

    # ---- persistence -------------------------------------------------

    def _fresh(self) -> dict[str, Any]:
        return {
            "admin_token": secrets.token_urlsafe(32),
            "signing_secret": secrets.token_urlsafe(32),
            "public_url": "",
            "config": copy.deepcopy(DEFAULT_CONFIG),
            "config_rev": 1,
            "devices": {},
            "groups": {},
            "inbox": [],
            "inbox_next_id": 1,
            "inbox_offset": 0,
        }

    def _load(self) -> dict[str, Any]:
        data = self._fresh()
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stored = {}
            if isinstance(stored, dict):
                data.update({k: v for k, v in stored.items() if k in data})
        self._data = data
        self._save()
        return data

    def _save(self) -> None:
        payload = json.dumps(self._data, indent=2, sort_keys=True).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---- secrets and simple settings -----------------------------------

    @property
    def admin_token(self) -> str:
        return str(self._data["admin_token"])

    @property
    def signing_secret(self) -> str:
        return str(self._data["signing_secret"])

    @property
    def config_rev(self) -> int:
        return int(self._data["config_rev"])

    @property
    def public_url(self) -> str:
        return str(self._data["public_url"])

    def set_public_url(self, url: str) -> str:
        """Store the public base URL. Empty, or an http(s) URL without trailing slash."""
        url = (url or "").strip().rstrip("/")
        if url and not url.startswith(("https://", "http://")):
            raise ValueError("public URL must start with https:// or http://")
        with self._lock:
            self._data["public_url"] = url
            self._save()
        return url

    # ---- config ------------------------------------------------------

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data["config"])

    def set_config(self, raw: Any) -> tuple[dict[str, Any], int]:
        """Normalize and store a config; bumps ``config_rev``. Raises ValueError on junk."""
        config = normalize_config(raw)
        with self._lock:
            self._data["config"] = config
            self._data["config_rev"] = int(self._data["config_rev"]) + 1
            self._save()
            return copy.deepcopy(config), int(self._data["config_rev"])

    # ---- devices and pairing -------------------------------------------

    def _prune_pair_codes(self) -> None:
        now = time.time()
        for code in [c for c, exp in self._pair_codes.items() if exp <= now]:
            del self._pair_codes[code]

    def create_pair_code(self) -> tuple[str, float]:
        """A single-use 8-character code valid for 10 minutes."""
        with self._lock:
            self._prune_pair_codes()
            code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(8))
            expires = time.time() + PAIR_TTL
            self._pair_codes[code] = expires
            return code, expires

    def redeem_pair_code(self, code: str, name: str) -> tuple[str, dict[str, Any]] | None:
        """Consume a pairing code and create a device. Returns (token, device) or None."""
        key = (code or "").strip().upper()
        with self._lock:
            self._prune_pair_codes()
            expires = self._pair_codes.pop(key, None)
            if expires is None or expires <= time.time():
                return None
            token = secrets.token_urlsafe(32)
            device = self._new_device(name, token_hash=hash_token(token), expires=0, hidden=False)
            self._save()
            return token, _public_device(device)

    def _new_device(self, name: str, *, token_hash: str, expires: float, hidden: bool) -> dict[str, Any]:
        now = time.time()
        device_id = "dev_" + secrets.token_hex(6)
        device = {
            "id": device_id,
            "name": name,
            "token_hash": token_hash,
            "created": now,
            "last_seen": now,
            "expires": expires,
            "hidden": hidden,
        }
        self._data["devices"][device_id] = device
        return device

    def create_preview_token(self) -> str:
        """A 12 h device token for the Studio emulator. Not listed in devices."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._new_device(
                "Studio preview",
                token_hash=hash_token(token),
                expires=time.time() + PREVIEW_TTL,
                hidden=True,
            )
            self._save()
        return token

    def device_for_token(self, token: str | None) -> dict[str, Any] | None:
        """Look up the device for a bearer token. Rejects expired devices."""
        if not token:
            return None
        digest = hash_token(token)
        with self._lock:
            found: dict[str, Any] | None = None
            for device in self._data["devices"].values():
                if hmac.compare_digest(str(device["token_hash"]), digest):
                    found = device
            if found is None:
                return None
            now = time.time()
            if found["expires"] and now > float(found["expires"]):
                return None
            if now - float(found["last_seen"]) >= LAST_SEEN_INTERVAL:
                found["last_seen"] = now
                self._save()
            return _public_device(found)

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return [_public_device(d) for d in self._data["devices"].values() if not d.get("hidden")]

    def remove_device(self, device_id: str) -> bool:
        with self._lock:
            if self._data["devices"].pop(device_id, None) is None:
                return False
            self._data["groups"].pop(device_id, None)
            self._save()
            return True

    def group_rooms(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data["groups"].get(device_id, {}))

    def save_group(self, device_id: str, room: dict[str, Any]) -> None:
        with self._lock:
            if device_id not in self._data["devices"]:
                return
            rooms = self._data["groups"].setdefault(device_id, {})
            previous = rooms.get(room["id"])
            rooms[room["id"]] = copy.deepcopy(room)
            try:
                self._save()
            except OSError:
                if previous is None:
                    rooms.pop(room["id"])
                else:
                    rooms[room["id"]] = previous
                raise

    # ---- inbox -------------------------------------------------------

    def _append_inbox(self, title: str, body: str, bot: str, level: str, ts: float) -> dict[str, Any]:
        item = {
            "id": int(self._data["inbox_next_id"]),
            "ts": ts,
            "bot": bot,
            "title": title,
            "body": body,
            "level": level if level in INBOX_LEVELS else "info",
        }
        self._data["inbox_next_id"] = item["id"] + 1
        items = self._data["inbox"]
        items.append(item)
        del items[:-INBOX_KEEP]
        return item

    def add_inbox(self, title: str, body: str, bot: str = "", level: str = "info") -> dict[str, Any]:
        with self._lock:
            item = self._append_inbox(title, body, bot, level, time.time())
            self._save()
            return dict(item)

    def inbox_after(self, after_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(i) for i in self._data["inbox"] if i["id"] > after_id]

    def ingest_inbox_file(self) -> int:
        """Add complete lines appended to ``inbox.jsonl`` since the last call.

        Returns the number of items added. Bad lines are skipped; a file that shrank
        below the stored offset is read again from the start.
        """
        with self._lock:
            try:
                size = self.inbox_path.stat().st_size
            except OSError:
                return 0
            offset = int(self._data["inbox_offset"])
            if offset > size:
                offset = 0
            if offset == size:
                return 0
            try:
                with self.inbox_path.open("rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read(INBOX_READ_CHUNK)
            except OSError:
                return 0
            end = chunk.rfind(b"\n")
            if end < 0:
                return 0
            complete = chunk[: end + 1]
            added = 0
            for line in complete.splitlines():
                item = _parse_inbox_line(line)
                if item is not None:
                    self._append_inbox(*item)
                    added += 1
            self._data["inbox_offset"] = offset + len(complete)
            self._save()
            return added


def _parse_inbox_line(line: bytes) -> tuple[str, str, str, str, float] | None:
    """Return (title, body, bot, level, ts) for a valid JSON line, else None."""
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    title = obj.get("title")
    if not isinstance(title, str):
        return None
    body = obj.get("body") if isinstance(obj.get("body"), str) else ""
    bot = obj.get("bot") if isinstance(obj.get("bot"), str) else ""
    level = obj.get("level") if isinstance(obj.get("level"), str) else "info"
    ts_raw = obj.get("ts")
    ts = float(ts_raw) if isinstance(ts_raw, (int, float)) and not isinstance(ts_raw, bool) else time.time()
    return title[:200], body[:2000], bot[:100], level, ts
