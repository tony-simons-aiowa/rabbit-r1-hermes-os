"""Token hashing, asset URL signing, loopback/admin checks and pairing rate limits."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

PROXY_HEADERS = (
    "X-Forwarded-For",
    "X-Real-IP",
    "CF-Connecting-IP",
    "Forwarded",
    "Tailscale-User-Login",
    "Tailscale-Funnel-Request",
)
SIG_LENGTH = 32
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def hash_token(token: str) -> str:
    """sha256 hex digest used to store device tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_asset(secret: str, path: str, exp: int) -> str:
    """HMAC-SHA256 over ``path + "\\n" + exp``, hex, first 32 chars."""
    message = f"{path}\n{exp}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return digest[:SIG_LENGTH]


def verify_asset(secret: str, path: str, exp: int, sig: str) -> bool:
    """True when ``sig`` matches and ``exp`` is in the future."""
    if exp < time.time():
        return False
    expected = sign_asset(secret, path, exp)
    return hmac.compare_digest(expected, sig)


def signed_url(secret: str, path: str, ttl: int = 86400) -> str:
    """``path?exp=…&sig=…`` valid for ``ttl`` seconds."""
    exp = int(time.time()) + ttl
    return f"{path}?exp={exp}&sig={sign_asset(secret, path, exp)}"


def is_local_request(request: "web.Request") -> bool:
    """Peer is loopback and no proxy/tunnel header is present."""
    peer = request.remote
    if not peer:
        return False
    try:
        if not ipaddress.ip_address(peer).is_loopback:
            return False
    except ValueError:
        return False
    if any(header in request.headers for header in PROXY_HEADERS):
        return False
    # Tunnels forward the public hostname; a local browser sends a loopback Host.
    # This also defeats DNS-rebinding pages that reach the bridge through the user's browser.
    host = (request.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
    return host in LOCAL_HOSTS


class RateLimiter:
    """Sliding-window failure counter keyed by string (e.g. peer IP)."""

    def __init__(self, max_failures: int = 5, window: float = 600) -> None:
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, list[float]] = defaultdict(list)

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def is_limited(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        self._recent(key)
        self._failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
