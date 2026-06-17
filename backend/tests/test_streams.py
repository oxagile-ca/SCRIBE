"""Tests for the disk-backed Stream registry.

These lock in the restart-survival invariant: a fresh consumer must be
able to recover the full event history from disk after the in-memory
Stream is gone, AND a live consumer must not miss events that arrive
during the replay/tail handoff.
"""
import asyncio
import json
import os

import pytest

from streams import StreamRegistry, replay_events_from_disk, END_MARKER


@pytest.fixture
def reg(tmp_path):
    return StreamRegistry(str(tmp_path))


def test_create_writes_jsonl(reg, tmp_path):
    s = reg.create("abc")
    s.append({"type": "log", "data": "hello"})
    s.append({"type": "log", "data": "world"})
    s.end()

    path = tmp_path / "abc.jsonl"
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 3
    events = [json.loads(line) for line in lines]
    assert events[0]["data"] == "hello"
    assert events[1]["data"] == "world"
    assert events[2]["type"] == END_MARKER
    # Monotonic seq numbers — the dedup invariant the SSE handler relies on.
    assert [e["_seq"] for e in events] == [1, 2, 3]


def test_append_after_end_is_noop(reg, tmp_path):
    s = reg.create("done")
    s.append({"type": "log", "data": "before"})
    s.end()
    s.append({"type": "log", "data": "after"})  # should be ignored

    events = list(replay_events_from_disk(str(tmp_path / "done.jsonl")))
    types = [e["type"] for e, _ in events]
    assert "log" in types
    assert types.count("log") == 1


def test_replay_from_disk_stops_at_end_marker(reg, tmp_path):
    """Replay must stop at END_MARKER even if extra junk is appended after."""
    s = reg.create("ended")
    s.append({"type": "log", "data": "a"})
    s.end()
    # Simulate a stray write after end (shouldn't happen, but be defensive).
    with open(tmp_path / "ended.jsonl", "a") as f:
        f.write(json.dumps({"type": "log", "data": "leak"}) + "\n")

    yielded = [e["type"] for e, _ in replay_events_from_disk(str(tmp_path / "ended.jsonl"))]
    assert yielded == ["log", END_MARKER]


@pytest.mark.asyncio
async def test_subscriber_gets_live_events(reg):
    s = reg.create("live")
    sub = s.subscribe()
    s.append({"type": "log", "data": "x"})
    s.append({"type": "log", "data": "y"})
    e1 = await sub.get()
    e2 = await sub.get()
    assert e1["data"] == "x"
    assert e2["data"] == "y"
    assert e1["_seq"] < e2["_seq"]


@pytest.mark.asyncio
async def test_subscriber_unsubscribe_stops_delivery(reg):
    s = reg.create("u")
    sub = s.subscribe()
    s.append({"type": "log", "data": "before"})
    s.unsubscribe(sub)
    s.append({"type": "log", "data": "after"})

    # First event still in queue, but nothing after unsubscribe.
    e1 = await sub.get()
    assert e1["data"] == "before"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.05)


@pytest.mark.asyncio
async def test_recreating_stream_id_ends_previous(reg):
    """A new POST with the same pipeline id should not leave the old
    subscribers hanging — they should receive an end marker."""
    s1 = reg.create("dup")
    sub = s1.subscribe()
    s2 = reg.create("dup")  # replaces s1
    assert s1.ended
    # Old subscriber sees the end marker pushed during reclaim.
    e = await asyncio.wait_for(sub.get(), timeout=0.1)
    assert e["type"] == END_MARKER
    assert s2 is not s1


def test_exists_on_disk_round_trip(reg):
    assert reg.exists_on_disk("nope") is False
    s = reg.create("yes")
    assert reg.exists_on_disk("yes") is True
    s.end()
    assert reg.exists_on_disk("yes") is True  # file remains for replay


def test_cleanup_old_respects_mtime(reg, tmp_path):
    old_path = tmp_path / "old.jsonl"
    new_path = tmp_path / "new.jsonl"
    old_path.write_text(json.dumps({"type": END_MARKER}) + "\n")
    new_path.write_text(json.dumps({"type": END_MARKER}) + "\n")

    # Backdate "old" by 30 days
    long_ago = old_path.stat().st_mtime - (30 * 86400)
    os.utime(old_path, (long_ago, long_ago))

    removed = reg.cleanup_old(retention_days=7)
    assert removed == 1
    assert not old_path.exists()
    assert new_path.exists()


@pytest.mark.asyncio
async def test_replay_then_tail_no_duplicates(reg, tmp_path):
    """Simulate the real SSE handler flow:
       1. Producer writes events 1..3
       2. Consumer subscribes (concurrent with producer)
       3. Consumer reads disk (events 1..3)
       4. Producer writes events 4..5
       5. Consumer drains queue but must skip 1..3 by seq, yield only 4..5

    This is the invariant that kills the race where events written between
    'open file' and 'attach subscriber' get lost or duplicated.
    """
    s = reg.create("rt")
    s.append({"type": "log", "data": "1"})
    s.append({"type": "log", "data": "2"})
    s.append({"type": "log", "data": "3"})

    sub = s.subscribe()
    # Replay disk
    yielded = []
    max_seq = 0
    for event, seq in replay_events_from_disk(str(tmp_path / "rt.jsonl")):
        if event.get("type") == END_MARKER:
            break
        yielded.append(event["data"])
        max_seq = max(max_seq, seq)
    assert yielded == ["1", "2", "3"]
    assert max_seq == 3

    # New events arrive after replay
    s.append({"type": "log", "data": "4"})
    s.append({"type": "log", "data": "5"})

    # Tail with dedup
    tailed = []
    while not sub.empty():
        event = sub.get_nowait()
        if event.get("_seq", 0) > max_seq:
            tailed.append(event["data"])
    assert tailed == ["4", "5"]
