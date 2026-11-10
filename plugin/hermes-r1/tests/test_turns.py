import asyncio
import time

import pytest

from hermes_r1.bridge.host import AssetFile, HostError, TurnHandle
from hermes_r1.bridge.mock_host import MockHost
from hermes_r1.bridge.store import Store
from hermes_r1.bridge.turns import TurnManager


class ScriptHost(MockHost):
    """MockHost whose run_turn replays a fixed list of events, then optionally raises."""

    def __init__(self, events, *, raise_after=None, voice=False):
        super().__init__(speed=0, voice=voice)
        self._events = events
        self._raise_after = raise_after

    async def run_turn(self, bot_id, session_id, text):
        async def gen():
            for event in self._events:
                yield event
            if self._raise_after is not None:
                raise self._raise_after

        return TurnHandle(session_id=session_id or "s_test", events=gen())


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


def manager(host, store):
    return TurnManager(host, store, store.state_dir / "audio")


async def drain(turns, turn_id, timeout=5):
    events, after = [], 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        batch, after, done = await turns.wait_events(turn_id, after, 0.5)
        events += batch
        if done:
            return events
    raise AssertionError("turn did not finish")


async def test_full_turn_seq_and_single_end(store):
    turns = manager(MockHost(speed=0), store)
    turn = await turns.start("dev1", "default", None, "hello there", False)
    events = await drain(turns, turn.id)
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(events) + 1))
    assert [e["t"] for e in events].count("end") == 1 and events[-1]["t"] == "end"
    assert events[-1]["status"] == "completed"
    assert events[-1]["text"] and events[-1]["usage"]["output"] > 0
    assert turns.overlay("default")["last"]["text"].startswith("You asked")
    await turns.close()


async def test_after_cursor_and_empty_wait(store):
    turns = manager(MockHost(speed=0), store)
    turn = await turns.start("dev1", "default", None, "hi", False)
    events = await drain(turns, turn.id)
    middle = events[2]["seq"]
    rest, nxt, done = await turns.wait_events(turn.id, middle, 0)
    assert [e["seq"] for e in rest] == [e["seq"] for e in events[middle:]]
    assert done and nxt == events[-1]["seq"]
    none, nxt2, done2 = await turns.wait_events(turn.id, events[-1]["seq"], 0)
    assert none == [] and nxt2 == events[-1]["seq"] and done2
    await turns.close()


async def test_busy_for_same_device_and_bot(store):
    turns = manager(MockHost(speed=0), store)
    first = await turns.start("dev1", "default", None, "approve this", False)
    with pytest.raises(HostError) as info:
        await turns.start("dev1", "default", None, "again", False)
    assert info.value.status == 429 and info.value.code == "busy"
    other = await turns.start("dev2", "default", None, "approve too", False)
    assert other.id != first.id
    await turns.close()


async def test_approval_flow_and_overlay(store):
    turns = manager(MockHost(speed=0), store)
    turn = await turns.start("dev1", "default", None, "approve the build", False)
    events, after = [], 0
    while not any(e["t"] == "ask" for e in events):
        batch, after, _ = await turns.wait_events(turn.id, after, 2)
        events += batch
    ask = next(e for e in events if e["t"] == "ask")
    assert ask["kind"] == "approval"
    assert [c["id"] for c in ask["choices"]] == ["once", "session", "always", "deny"]
    assert turns.overlay("default")["state"] == "ask"
    await turns.respond(turn.id, ask["request_id"], choice="once")
    events += await drain(turns, turn.id)
    assert [e["t"] for e in events].count("ask_done") == 1
    assert events[-1]["status"] == "completed" and "Approved" in events[-1]["text"]
    assert turns.overlay("default")["state"] == "idle"
    assert turns.overlay("default")["active"] is True  # ended within 90 s
    await turns.close()


async def test_respond_unknown_request_and_finished_turn(store):
    turns = manager(MockHost(speed=0), store)
    turn = await turns.start("dev1", "default", None, "approve", False)
    with pytest.raises(HostError) as info:
        await turns.respond(turn.id, "r_nope", choice="once")
    assert info.value.status == 404
    await turns.stop(turn.id)
    with pytest.raises(HostError) as info:
        await turns.respond(turn.id, "r_nope", choice="once")
    assert info.value.status == 409
    await turns.close()


async def test_stop_gives_cancelled_end(store):
    turns = manager(MockHost(speed=0), store)
    turn = await turns.start("dev1", "default", None, "approve something", False)
    events, after = [], 0
    while not any(e["t"] == "ask" for e in events):
        batch, after, _ = await turns.wait_events(turn.id, after, 2)
        events += batch
    await turns.stop(turn.id)
    events += await drain(turns, turn.id)
    assert events[-1]["t"] == "end" and events[-1]["status"] == "cancelled"
    assert [e["t"] for e in events].count("end") == 1
    await turns.close()


async def test_long_poll_returns_early_on_event(store):
    turns = manager(MockHost(speed=0), store)
    turn = await turns.start("dev1", "default", None, "approve it", False)
    events, after = [], 0
    while not any(e["t"] == "ask" for e in events):
        batch, after, _ = await turns.wait_events(turn.id, after, 2)
        events += batch
    ask = next(e for e in events if e["t"] == "ask")

    poll = asyncio.create_task(turns.wait_events(turn.id, after, 10))
    await asyncio.sleep(0.1)
    assert not poll.done()
    started = time.monotonic()
    await turns.respond(turn.id, ask["request_id"], choice="deny")
    batch, _, _ = await asyncio.wait_for(poll, 5)
    assert time.monotonic() - started < 2
    assert batch and batch[0]["t"] == "ask_done"
    await turns.close()


async def test_device_isolation(store):
    turns = manager(MockHost(speed=0), store)
    turn = await turns.start("dev1", "default", None, "hi", False)
    with pytest.raises(HostError) as info:
        await turns.wait_events(turn.id, 0, 0, device_id="dev2")
    assert info.value.status == 404
    await turns.close()


async def test_host_exception_becomes_failed_end(store):
    host = ScriptHost([{"t": "start", "session_id": "s"}, {"t": "delta", "text": "partial "}],
                      raise_after=RuntimeError("socket reset"))
    turns = manager(host, store)
    turn = await turns.start("dev1", "default", None, "x", False)
    events = await drain(turns, turn.id)
    end = events[-1]
    assert end["t"] == "end" and end["status"] == "failed"
    assert end["text"] == "partial " and "socket reset" in end["error"]
    assert [e["t"] for e in events].count("end") == 1
    await turns.close()


async def test_stream_without_end_becomes_failed(store):
    host = ScriptHost([{"t": "start", "session_id": "s"}, {"t": "delta", "text": "abc"}])
    turns = manager(host, store)
    turn = await turns.start("dev1", "default", None, "x", False)
    events = await drain(turns, turn.id)
    assert events[-1]["status"] == "failed" and events[-1]["text"] == "abc"
    assert "without an end" in events[-1]["error"]
    await turns.close()


async def test_host_end_then_extra_events_ignored(store):
    host = ScriptHost([
        {"t": "start", "session_id": "s"},
        {"t": "end", "status": "bogus", "text": "", "error": "", "usage": {"input": 1, "output": 2}},
        {"t": "delta", "text": "late"},
    ])
    turns = manager(host, store)
    turn = await turns.start("dev1", "default", None, "x", False)
    events = await drain(turns, turn.id)
    assert events[-1]["status"] == "failed"
    assert [e["t"] for e in events] == ["start", "end"]
    await turns.close()


async def test_speak_inserts_speech_before_end(store):
    turns = manager(MockHost(speed=0, voice=True), store)
    turn = await turns.start("dev1", "default", None, "say something nice", True)
    events = await drain(turns, turn.id)
    kinds = [e["t"] for e in events]
    assert "speech" in kinds and kinds.index("speech") == len(kinds) - 2
    speech = events[-2]
    assert speech["mime"] == "audio/wav" and speech["url"].startswith("/api/v1/assets/audio/")
    assert turns.audio_file(speech["url"].split("/")[5].split("?")[0]) is not None
    await turns.close()


async def test_speech_failure_does_not_fail_turn(store):
    class BrokenTTS(MockHost):
        async def synthesize(self, text, out_dir):
            raise RuntimeError("tts exploded")

    turns = manager(BrokenTTS(speed=0, voice=True), store)
    turn = await turns.start("dev1", "default", None, "hello", True)
    events = await drain(turns, turn.id)
    assert events[-1]["status"] == "completed"
    assert "speech" not in [e["t"] for e in events]
    await turns.close()


async def test_audio_prune_removes_old_files(store, tmp_path):
    turns = manager(MockHost(speed=0), store)
    path = tmp_path / "old.wav"
    path.write_bytes(b"RIFF")
    aid = turns.register_audio(AssetFile(path=path, content_type="audio/wav"))
    turns._audio[aid] = (turns._audio[aid][0], time.time() - 4000)
    turns._prune()
    assert turns.audio_file(aid) is None and not path.exists()
    await turns.close()
