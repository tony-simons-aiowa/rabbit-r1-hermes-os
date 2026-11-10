import time
from unittest import mock

from aiohttp.test_utils import make_mocked_request

from hermes_r1.bridge.auth import (
    RateLimiter,
    hash_token,
    is_local_request,
    sign_asset,
    signed_url,
    verify_asset,
)

SECRET = "test-secret"
PATH = "/api/v1/assets/avatar/coder"


def _split(url: str) -> tuple[int, str]:
    query = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))
    return int(query["exp"]), query["sig"]


def test_hash_token_is_sha256_hex():
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_signed_url_roundtrip():
    url = signed_url(SECRET, PATH)
    assert url.startswith(PATH + "?exp=")
    exp, sig = _split(url)
    assert len(sig) == 32
    assert verify_asset(SECRET, PATH, exp, sig)


def test_tampered_path_secret_or_sig_rejected():
    exp, sig = _split(signed_url(SECRET, PATH))
    assert not verify_asset(SECRET, PATH + "x", exp, sig)
    assert not verify_asset("other", PATH, exp, sig)
    assert not verify_asset(SECRET, PATH, exp, "0" * 32)


def test_expired_rejected():
    exp = int(time.time()) - 5
    assert not verify_asset(SECRET, PATH, exp, sign_asset(SECRET, PATH, exp))


def test_rate_limiter_window():
    limiter = RateLimiter(max_failures=3, window=600)
    for _ in range(2):
        limiter.record_failure("ip")
    assert not limiter.is_limited("ip")
    limiter.record_failure("ip")
    assert limiter.is_limited("ip")
    assert not limiter.is_limited("other")
    limiter.reset("ip")
    assert not limiter.is_limited("ip")


def _request(peer: str, headers: dict[str, str] | None = None):
    transport = mock.Mock()
    transport.get_extra_info.return_value = (peer, 1234)
    merged = {"Host": "127.0.0.1:8717", **(headers or {})}
    return make_mocked_request("GET", "/api/v1/admin/state", headers=merged, transport=transport)


def test_is_local_request():
    assert is_local_request(_request("127.0.0.1"))
    assert is_local_request(_request("::1"))
    assert not is_local_request(_request("192.168.1.5"))
    for header in ("X-Forwarded-For", "CF-Connecting-IP", "Tailscale-User-Login", "Forwarded"):
        assert not is_local_request(_request("127.0.0.1", {header: "x"}))
    assert is_local_request(_request("127.0.0.1", {"Host": "localhost:8717"}))
    assert not is_local_request(_request("127.0.0.1", {"Host": "evil.example.com"}))
    assert not is_local_request(_request("127.0.0.1", {"Host": "my-mac.tailnet.ts.net"}))
