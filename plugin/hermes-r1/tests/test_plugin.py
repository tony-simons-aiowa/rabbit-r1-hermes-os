"""Plugin registration, the r1_notify tool, and the `hermes r1` CLI logic."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

import hermes_r1
from hermes_r1 import cli

FAKE_KEY_ALPHA = "alpha-secret-value-7f3c"


class FakeCtx:
    def __init__(self, fail_cli: bool = False, fail_tool: bool = False) -> None:
        self.cli_calls: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.fail_cli = fail_cli
        self.fail_tool = fail_tool

    def register_cli_command(self, **kwargs: Any) -> None:
        if self.fail_cli:
            raise RuntimeError("boom")
        self.cli_calls.append(kwargs)

    def register_tool(self, **kwargs: Any) -> None:
        if self.fail_tool:
            raise RuntimeError("boom")
        self.tool_calls.append(kwargs)


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "r1-state"
    monkeypatch.setenv("HERMES_R1_HOME", str(target))
    monkeypatch.delenv("HERMES_PROFILE_NAME", raising=False)
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return target


# --------------------------------------------------------------------------- register()


def test_register_records_cli_and_tool() -> None:
    ctx = FakeCtx()
    hermes_r1.register(ctx)
    assert len(ctx.cli_calls) == 1 and len(ctx.tool_calls) == 1

    command = ctx.cli_calls[0]
    assert command["name"] == "r1"
    assert command["help"] == "Rabbit R1 bridge: serve, pair, setup, doctor"
    assert callable(command["setup_fn"]) and callable(command["handler_fn"])

    tool = ctx.tool_calls[0]
    assert tool["name"] == "r1_notify" and tool["toolset"] == "r1"
    assert tool["emoji"] == "\N{BELL}"
    assert callable(tool["handler"])
    assert tool["schema"]["name"] == "r1_notify"
    assert tool["schema"]["parameters"]["required"] == ["title"]
    assert tool["schema"]["parameters"]["properties"]["level"]["enum"] == ["info", "success", "warn"]


def test_register_isolates_failures() -> None:
    ctx = FakeCtx(fail_cli=True)
    hermes_r1.register(ctx)  # must not raise
    assert ctx.cli_calls == [] and len(ctx.tool_calls) == 1

    ctx = FakeCtx(fail_tool=True)
    hermes_r1.register(ctx)
    assert len(ctx.cli_calls) == 1 and ctx.tool_calls == []


def test_package_root_has_no_heavy_imports() -> None:
    source = Path(hermes_r1.__file__).read_text()
    assert "import aiohttp" not in source
    assert "from .bridge" not in source and "import yaml" not in source


# --------------------------------------------------------------------------- r1_notify


def test_notify_appends_valid_json_line(state_home: Path) -> None:
    result = json.loads(hermes_r1._r1_notify_handler({"title": "Build done", "body": "All green", "level": "success"}))
    assert result == {"success": True}

    lines = (state_home / "inbox.jsonl").read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert set(record) == {"ts", "bot", "title", "body", "level"}
    assert record["title"] == "Build done" and record["body"] == "All green" and record["level"] == "success"
    assert record["bot"] == ""
    assert isinstance(record["ts"], float)


def test_notify_appends_and_truncates(state_home: Path) -> None:
    hermes_r1._r1_notify_handler({"title": "x" * 100, "body": "y" * 900, "level": "weird"})
    hermes_r1._r1_notify_handler({"title": "second"})
    lines = [json.loads(line) for line in (state_home / "inbox.jsonl").read_text().splitlines()]
    assert len(lines) == 2
    assert len(lines[0]["title"]) == 60 and len(lines[0]["body"]) == 400
    assert lines[0]["level"] == "info"
    assert lines[1]["title"] == "second" and lines[1]["body"] == "" and lines[1]["level"] == "info"


def test_notify_state_dir_is_private(state_home: Path) -> None:
    hermes_r1._r1_notify_handler({"title": "private"})
    assert stat.S_IMODE(os.stat(state_home).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(state_home / "inbox.jsonl").st_mode) == 0o600


def test_notify_requires_title(state_home: Path) -> None:
    result = json.loads(hermes_r1._r1_notify_handler({"title": "   "}))
    assert result["success"] is False
    assert not (state_home / "inbox.jsonl").exists()


def test_notify_bot_from_profile_env(state_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE_NAME", "coach")
    hermes_r1._r1_notify_handler({"title": "hi"})
    assert json.loads((state_home / "inbox.jsonl").read_text())["bot"] == "coach"


def test_state_dir_fallbacks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("HERMES_R1_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    assert hermes_r1._state_dir() == tmp_path / "hh" / "r1"
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert hermes_r1._state_dir() == Path.home() / ".hermes" / "r1"


# --------------------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes r1")
    cli.setup_parser(parser)
    return parser


def _make_home(root: Path) -> Path:
    home = root / "hermes"
    (home / "profiles" / "alpha").mkdir(parents=True)
    (home / "profiles" / "alpha" / "config.yaml").write_text("model: x\n")
    (home / "profiles" / "alpha" / ".env").write_text("OTHER=1\n")
    (home / "profiles" / "beta").mkdir(parents=True)
    (home / "profiles" / "beta" / "config.yaml").write_text("model: y\n")
    (home / "profiles" / "beta" / ".env").write_text(f"API_SERVER_KEY={FAKE_KEY_ALPHA}\n")
    (home / ".env").write_text("API_SERVER_KEY=root-key-not-printed\n")
    return home


def test_setup_parser_builds_subcommands() -> None:
    args = _parser().parse_args(["serve", "--mock", "--port", "9000", "--tunnel", "cloudflared"])
    assert args.r1_command == "serve" and args.mock is True and args.port == 9000
    assert cli._serve_argv(args) == ["--mock", "--port", "9000", "--tunnel", "cloudflared"]


def test_setup_dry_run_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = _make_home(tmp_path)
    before = (home / "profiles" / "alpha" / ".env").read_text()
    args = _parser().parse_args(["setup", "--dry-run", "--hermes-home", str(home)])
    assert cli.handle(args) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" not in out
    assert "Dry run" in out
    assert (home / "profiles" / "alpha" / ".env").read_text() == before
    assert not list((home / "profiles" / "alpha").glob(".env.bak-*"))


def test_setup_yes_adds_key_with_backup_and_hides_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = _make_home(tmp_path)
    beta_before = (home / "profiles" / "beta" / ".env").read_text()
    args = _parser().parse_args(["setup", "--yes", "--hermes-home", str(home)])
    assert cli.handle(args) == 0
    out = capsys.readouterr().out

    alpha_env = home / "profiles" / "alpha" / ".env"
    text = alpha_env.read_text()
    assert text.startswith("OTHER=1\n")
    generated = [line.split("=", 1)[1] for line in text.splitlines() if line.startswith("API_SERVER_KEY=")]
    assert len(generated) == 1 and len(generated[0]) >= 40
    assert stat.S_IMODE(os.stat(alpha_env).st_mode) == 0o600

    backups = list((home / "profiles" / "alpha").glob(".env.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "OTHER=1\n"

    assert (home / "profiles" / "beta" / ".env").read_text() == beta_before  # already keyed: untouched
    assert generated[0] not in out and FAKE_KEY_ALPHA not in out and "root-key-not-printed" not in out
    assert "Updated profiles: alpha" in out
    assert "Restart the gateway to apply: hermes gateway restart" in out


def test_setup_creates_missing_env_with_mode_0600(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "hermes"
    (home / "profiles" / "gamma").mkdir(parents=True)
    (home / "profiles" / "gamma" / "SOUL.md").write_text("soul")
    assert cli.handle(_parser().parse_args(["setup", "--yes", "--hermes-home", str(home)])) == 0
    env = home / "profiles" / "gamma" / ".env"
    assert env.read_text().startswith("API_SERVER_KEY=")
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    assert not list((home / "profiles" / "gamma").glob(".env.bak-*"))
    generated = env.read_text().split("=", 1)[1].strip()
    assert generated not in capsys.readouterr().out


def test_setup_without_yes_and_no_answer_changes_nothing(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    before = (home / "profiles" / "alpha" / ".env").read_text()
    lines: list[str] = []
    code = cli.run_setup(home, yes=False, confirm=lambda prompt: "n", out=lines.append)
    assert code == 0
    assert (home / "profiles" / "alpha" / ".env").read_text() == before
    assert any("No changes made" in line for line in lines)


def test_doctor_reports_without_printing_secrets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = _make_home(tmp_path)
    (home / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n")
    static = tmp_path / "static"
    (static / "app").mkdir(parents=True)
    (static / "app" / "index.html").write_text("<!doctype html>")

    checks = cli.doctor_checks(
        home, "http://127.0.0.1:1", static,
        probe=lambda url: False, which=lambda name: "/usr/bin/cloudflared" if name == "cloudflared" else None,
    )
    texts = {check.text: check.ok for check in checks}
    lines = [check.line() for check in checks]
    assert all(line[0] in "✓✗" for line in lines)

    def find(fragment: str) -> bool:
        matches = [ok for text, ok in texts.items() if fragment in text]
        assert len(matches) == 1, fragment
        return matches[0]

    assert find("Hermes API server") is False
    assert find("default profile has API_SERVER_KEY") is True
    assert find("profile 'alpha' has no API_SERVER_KEY") is False
    assert find("profile 'beta' has its own API_SERVER_KEY") is True
    assert find("multiplex_profiles") is True
    assert find("cloudflared found") is True
    assert find("tailscale not found") is False
    assert find("static/app is built") is True
    assert find("static/studio is not built") is False

    printed = "\n".join(lines)
    assert FAKE_KEY_ALPHA not in printed and "root-key-not-printed" not in printed


def test_doctor_command_output_has_no_secret(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    home = _make_home(tmp_path)
    monkeypatch.setattr(cli, "_hermes_reachable", lambda url: False)
    code = cli.handle(_parser().parse_args(["doctor", "--hermes-home", str(home)]))
    assert code == 1
    out = capsys.readouterr().out
    assert "✓" in out and "✗" in out
    assert FAKE_KEY_ALPHA not in out and "root-key-not-printed" not in out


def test_status_tolerates_missing_state(tmp_path: Path) -> None:
    lines = cli.status_lines(tmp_path / "missing", 8717, probe=lambda port: False)
    assert any("not running" in line for line in lines)
    assert any(line.strip().endswith("0") for line in lines if line.startswith("devices"))


def test_status_reads_state_json(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text(json.dumps({"public_url": "https://r1.example", "devices": {"d1": {}, "d2": {}}}))
    lines = cli.status_lines(tmp_path, 8717, probe=lambda port: True)
    assert "https://r1.example" in "\n".join(lines)
    assert "devices:     2" in lines
    assert any("running" in line and "not" not in line for line in lines)
