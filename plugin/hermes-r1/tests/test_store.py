import json
import os
import stat
import time

import pytest

from hermes_r1.bridge.store import FINISHED_KEEP, Store, default_state_dir


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


def test_secrets_persist_across_instances(tmp_path):
    first = Store(tmp_path / "s")
    second = Store(tmp_path / "s")
    assert first.admin_token == second.admin_token
    assert first.signing_secret == second.signing_secret
    assert len(first.admin_token) >= 40


def test_state_file_is_private(tmp_path):
    Store(tmp_path / "s")
    path = tmp_path / "s" / "state.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "s").st_mode) == 0o700


def test_pair_code_format_and_single_use(store):
    code, expires = store.create_pair_code()
    assert len(code) == 8
    assert set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    assert expires - time.time() == pytest.approx(600, abs=5)
    token, device = store.redeem_pair_code(code, "R1")
    assert device["name"] == "R1" and device["id"].startswith("dev_")
    assert store.device_for_token(token)["id"] == device["id"]
    assert store.redeem_pair_code(code, "again") is None


def test_pair_code_bad_and_expired(store):
    assert store.redeem_pair_code("NOPE1234", "x") is None
    code, _ = store.create_pair_code()
    store._pair_codes[code] = time.time() - 1
    assert store.redeem_pair_code(code, "x") is None


def test_device_token_hashed_and_revocable(store):
    code, _ = store.create_pair_code()
    token, device = store.redeem_pair_code(code, "R1")
    raw = (store.state_dir / "state.json").read_text()
    assert token not in raw
    assert store.device_for_token(token + "x") is None
    assert store.device_for_token(None) is None
    assert store.remove_device(device["id"]) is True
    assert store.device_for_token(token) is None
    assert store.remove_device(device["id"]) is False


def test_finished_results_are_bounded_newest_first_and_removed_with_device(store):
    code, _ = store.create_pair_code()
    _, device = store.redeem_pair_code(code, "R1")
    for i in range(FINISHED_KEEP + 3):
        store.add_finished(device["id"], {"kind": "chat", "turn_id": str(i), "ts": i})
    entries = store.finished(device["id"])
    assert len(entries) == FINISHED_KEEP
    assert entries[0]["turn_id"] == str(FINISHED_KEEP + 2)
    assert entries[-1]["turn_id"] == "3"
    assert store.remove_device(device["id"])
    assert store.finished(device["id"]) == []


def test_group_completion_write_failure_leaves_room_and_fleet_consistent(store, monkeypatch):
    code, _ = store.create_pair_code()
    _, device = store.redeem_pair_code(code, "R1")
    device_id = device["id"]
    room = {"id": "room", "rounds": [{"replies": [{"status": "pending"}]}]}
    store.save_group(device_id, room)
    completed = {"id": "room", "rounds": [{"replies": [{"status": "completed"}], "finished_ts": 1}]}

    def disk_full():
        raise OSError("Disk full")

    monkeypatch.setattr(store, "_save", disk_full)
    with pytest.raises(OSError):
        store.finish_group_round(device_id, completed, {"kind": "group", "room_id": "room", "ts": 1})
    assert store.group_rooms(device_id) == {"room": room}
    assert store.finished(device_id) == []


def test_preview_token_hidden_and_expires(store):
    token = store.create_preview_token()
    assert store.device_for_token(token)["name"] == "Studio preview"
    assert store.list_devices() == []
    store._data["devices"][next(iter(store._data["devices"]))]["expires"] = time.time() - 1
    assert store.device_for_token(token) is None


def test_list_devices_hides_token_hash(store):
    code, _ = store.create_pair_code()
    store.redeem_pair_code(code, "R1")
    devices = store.list_devices()
    assert len(devices) == 1 and "token_hash" not in devices[0]


def test_config_bumps_rev_and_normalizes(store):
    rev0 = store.config_rev
    cfg, rev = store.set_config({"font_scale": 5, "junk": 1})
    assert rev == rev0 + 1 and store.config_rev == rev
    assert cfg["font_scale"] == 1.3 and "junk" not in cfg
    assert store.get_config() == cfg
    with pytest.raises(ValueError):
        store.set_config("nope")


def test_public_url_validation(store):
    assert store.set_public_url("https://r1.example.com/") == "https://r1.example.com"
    assert store.public_url == "https://r1.example.com"
    assert store.set_public_url("") == ""
    with pytest.raises(ValueError):
        store.set_public_url("javascript:alert(1)")


def test_inbox_add_and_after(store):
    a = store.add_inbox("one", "b", bot="coder", level="warn")
    b = store.add_inbox("two", "", level="bogus")
    assert b["level"] == "info"
    assert [i["id"] for i in store.inbox_after(a["id"])] == [b["id"]]
    assert store.inbox_after(0)[0]["bot"] == "coder"


def test_inbox_keeps_last_100(store):
    for i in range(120):
        store.add_inbox(f"t{i}", "")
    items = store.inbox_after(0)
    assert len(items) == 100 and items[0]["title"] == "t20"


def test_ingest_file_reads_complete_lines_only(store):
    path = store.inbox_path
    good = {"ts": 1.0, "bot": "coder", "title": "Build done", "body": "ok", "level": "success"}
    with path.open("w") as fh:
        fh.write(json.dumps(good) + "\n")
        fh.write("not json\n")
        fh.write(json.dumps(["list"]) + "\n")
        fh.write(json.dumps({"title": "bad level", "level": "x"}) + "\n")
        fh.write('{"title": "partial"')
    assert store.ingest_inbox_file() == 2
    items = store.inbox_after(0)
    assert [i["title"] for i in items] == ["Build done", "bad level"]
    assert items[0]["level"] == "success" and items[1]["level"] == "info"
    assert store.ingest_inbox_file() == 0
    with path.open("a") as fh:
        fh.write(', "level": "warn"}\n')
    assert store.ingest_inbox_file() == 1
    assert store.inbox_after(items[-1]["id"])[0]["title"] == "partial"


def test_ingest_missing_file_and_truncation(store):
    assert store.ingest_inbox_file() == 0
    store.inbox_path.write_text(json.dumps({"title": "a"}) + "\n")
    assert store.ingest_inbox_file() == 1
    store.inbox_path.write_text('{"title":"b"}\n')  # shorter than the stored offset
    assert store.ingest_inbox_file() == 1
    assert store.inbox_after(0)[-1]["title"] == "b"


def test_default_state_dir_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_R1_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_state_dir() == tmp_path / ".hermes" / "r1"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "h"))
    assert default_state_dir() == tmp_path / "h" / "r1"
    monkeypatch.setenv("HERMES_R1_HOME", str(tmp_path / "x"))
    assert default_state_dir() == tmp_path / "x"
