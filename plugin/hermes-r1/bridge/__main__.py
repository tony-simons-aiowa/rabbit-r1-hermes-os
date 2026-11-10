"""Command-line entry point: run the bridge with MockHost or the real Hermes host.

    python -m hermes_r1.bridge --mock
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from .mock_host import MockHost
from .server import APP_LOCAL_URL, create_app
from .store import Store, default_state_dir

PLUGIN_DIR = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"
DEFAULT_PORT = 8717
TUNNEL_TIMEOUT = 30.0
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

log = logging.getLogger("hermes_r1.bridge")


@dataclass
class Tunnel:
    """A running tunnel: its public URL (if any) and the child process to stop on exit."""

    url: str | None = None
    proc: asyncio.subprocess.Process | None = None
    drain: asyncio.Task[None] | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-r1-bridge", description="Hermes R1 bridge server")
    parser.add_argument("--mock", action="store_true", help="use scripted demo bots (no Hermes needed)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default 8717)")
    parser.add_argument("--public-url", default="", metavar="URL", help="public HTTPS base URL of this bridge")
    parser.add_argument("--tunnel", choices=["none", "cloudflared", "tailscale"], default="none",
                        help="expose the bridge through a tunnel and use its URL as the public URL")
    parser.add_argument("--allow-remote-admin", action="store_true",
                        help="allow admin routes from non-loopback clients (unsafe)")
    parser.add_argument("--hermes-url", default="http://127.0.0.1:8642", help="Hermes API server URL")
    parser.add_argument("--hermes-home", default="", metavar="PATH",
                        help="Hermes home (default $HERMES_HOME or ~/.hermes)")
    parser.add_argument("--state-dir", default="", metavar="PATH", help="bridge state directory")
    parser.add_argument("--no-open", action="store_true", help="reserved; the bridge never opens a browser")
    return parser


def _state_dir(args: argparse.Namespace) -> Path:
    if args.state_dir:
        return Path(args.state_dir).expanduser()
    base = default_state_dir()
    if args.mock:
        return Path(str(base) + "-mock")
    return base


def _build_host(args: argparse.Namespace) -> Any | None:
    if args.mock:
        return MockHost(voice=True)
    try:
        from .hermes_host import HermesHost
    except ImportError as exc:
        print(f"error: the Hermes host is unavailable: {exc}", file=sys.stderr)
        print("hint: run with --mock to try the bridge without Hermes.", file=sys.stderr)
        return None
    hermes_home = Path(args.hermes_home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return HermesHost(hermes_home=hermes_home.expanduser(), api_url=args.hermes_url)


# ---- tunnels -----------------------------------------------------------

async def _drain(stream: asyncio.StreamReader) -> None:
    while await stream.readline():
        pass


async def _read_tunnel_url(proc: asyncio.subprocess.Process) -> str | None:
    assert proc.stdout is not None

    async def scan() -> str | None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return None
            match = TUNNEL_URL_RE.search(line.decode("utf-8", "replace"))
            if match:
                return match.group(0)

    try:
        return await asyncio.wait_for(scan(), TUNNEL_TIMEOUT)
    except asyncio.TimeoutError:
        return None


async def _start_cloudflared(port: int) -> Tunnel:
    try:
        proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError:
        print("warning: cloudflared is not installed; continuing without a public URL.")
        return Tunnel()
    url = await _read_tunnel_url(proc)
    if url is None:
        print("warning: cloudflared did not report a public URL within 30 s; continuing without one.")
        await _stop_process(proc)
        return Tunnel()
    return Tunnel(url=url, proc=proc, drain=asyncio.create_task(_drain(proc.stdout)))


async def _run_capture(*args: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 30)
    except asyncio.TimeoutError:
        await _stop_process(proc)
        raise
    return proc.returncode or 0, out, err


async def _start_tailscale(port: int) -> Tunnel:
    try:
        code, _, err = await _run_capture("tailscale", "funnel", "--bg", str(port))
        if code != 0:
            print(f"warning: tailscale funnel failed: {err.decode('utf-8', 'replace').strip()}")
            return Tunnel()
        _, status, _ = await _run_capture("tailscale", "status", "--json")
        dns_name = str(json.loads(status)["Self"]["DNSName"]).rstrip(".")
    except (OSError, asyncio.TimeoutError, KeyError, ValueError, TypeError):
        print("warning: could not read the Tailscale DNS name; continuing without a public URL.")
        return Tunnel()
    return Tunnel(url=f"https://{dns_name}")


async def _stop_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


async def _stop_tunnel(tunnel: Tunnel) -> None:
    if tunnel.drain is not None:
        tunnel.drain.cancel()
    if tunnel.proc is not None:
        await _stop_process(tunnel.proc)


async def _open_tunnel(kind: str, port: int) -> Tunnel:
    if kind == "cloudflared":
        return await _start_cloudflared(port)
    if kind == "tailscale":
        return await _start_tailscale(port)
    return Tunnel()


# ---- serving -----------------------------------------------------------

def _banner(args: argparse.Namespace, store: Store, public: str, mock: bool) -> str:
    local = f"http://127.0.0.1:{args.port}"
    lines = [
        f"Hermes R1 bridge {VERSION}" + (" (mock mode, scripted demo bots)" if mock else ""),
        f"  local:   {local}",
        f"  studio:  {local}/studio/#admin={store.admin_token}",
    ]
    if public:
        lines.append(f"  public:  {public}")
    else:
        lines.append("  public:  none. Set one in Studio > Devices, or pass --public-url / --tunnel.")
    lines.append(f"  state:   {store.state_dir}")
    return "\n".join(lines)


async def _serve(args: argparse.Namespace, app: web.Application, store: Store) -> int:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    tunnel = Tunnel()
    try:
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()
        tunnel = await _open_tunnel(args.tunnel, args.port)
        if tunnel.url:
            store.set_public_url(tunnel.url)
        print(_banner(args, store, store.public_url, args.mock), flush=True)
        await asyncio.Event().wait()
    finally:
        await _stop_tunnel(tunnel)
        await runner.cleanup()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = Store(_state_dir(args))
    if args.public_url:
        try:
            store.set_public_url(args.public_url)
        except ValueError as exc:
            print(f"error: --public-url: {exc}", file=sys.stderr)
            return 2
    host = _build_host(args)
    if host is None:
        return 2
    app = create_app(host, store, static_dir=PLUGIN_DIR / "static",
                     allow_remote_admin=args.allow_remote_admin, version=VERSION)
    app[APP_LOCAL_URL] = f"http://127.0.0.1:{args.port}"
    try:
        return asyncio.run(_serve(args, app, store))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
