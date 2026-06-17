"""Disk-backed SSE stream storage with in-memory subscriber fan-out.

Why this exists: the previous design held every in-flight pipeline's event
queue in process memory. Any `uvicorn --reload` (now the default) killed
every running pipeline silently — the EventSource on the frontend errored
out with no way to recover the 40-min deploy in progress.

This module persists every event to `~/qa-dashboard/streams/<id>.jsonl`
as it's produced, so reconnecting clients (or completely new browser
sessions) can replay the full history from disk and then tail any new
events that arrive after the replay catches up.

Events get a monotonically-increasing `_seq` so the SSE handler can
dedup the replay/tail boundary without race conditions.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional


END_MARKER = "_end"


class Stream:
    """One pipeline / build / chat session's event log.

    Lifecycle:
      s = Stream.create(stream_id, streams_dir) — open file for append
      s.append(event)                            — write + fan out
      s.end()                                    — terminal sentinel + close
      s.subscribe() / s.unsubscribe(q)           — for live tail consumers
    """

    def __init__(self, stream_id: str, path: str):
        self.id = stream_id
        self.path = path
        self.ended = False
        self.subscribers: set[asyncio.Queue] = set()
        self._file = None
        self._seq = 0

    @classmethod
    def create(cls, stream_id: str, streams_dir: str) -> "Stream":
        os.makedirs(streams_dir, exist_ok=True)
        path = os.path.join(streams_dir, f"{stream_id}.jsonl")
        stream = cls(stream_id, path)
        # Truncate if a prior aborted stream left a partial file. Fresh streams
        # always start from empty so seq numbers stay monotonic per stream id.
        stream._file = open(path, "w", buffering=1, encoding="utf-8")
        return stream

    def append(self, event: dict) -> None:
        """Persist one event and notify live subscribers.

        Synchronous file I/O is fine here: events are small JSON dicts at
        human-paced rates (a handful per second at worst). The flush is
        important — without it a uvicorn reload could lose the tail of the
        log between the last write and the SIGTERM.
        """
        if self.ended:
            return
        self._seq += 1
        payload = {**event, "_seq": self._seq}
        if self._file:
            self._file.write(json.dumps(payload) + "\n")
            self._file.flush()
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer; SSE replay will catch them up on reconnect.
                pass

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        self._seq += 1
        terminal = {"type": END_MARKER, "_seq": self._seq}
        if self._file:
            try:
                self._file.write(json.dumps(terminal) + "\n")
                self._file.flush()
                self._file.close()
            except Exception:
                pass
            self._file = None
        for q in list(self.subscribers):
            try:
                q.put_nowait(terminal)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)


class StreamRegistry:
    """Tracks live Stream objects by id. Disk is the source of truth — the
    in-memory registry only matters for live fan-out to subscribers."""

    def __init__(self, streams_dir: str):
        self.streams_dir = streams_dir
        self._live: dict[str, Stream] = {}
        os.makedirs(streams_dir, exist_ok=True)

    def create(self, stream_id: str) -> Stream:
        if stream_id in self._live and not self._live[stream_id].ended:
            # Repeated POST for the same pipeline id (e.g. retry). End the
            # previous one cleanly so subscribers don't hang on a stale stream.
            self._live[stream_id].end()
        stream = Stream.create(stream_id, self.streams_dir)
        self._live[stream_id] = stream
        return stream

    def get(self, stream_id: str) -> Optional[Stream]:
        return self._live.get(stream_id)

    def path_for(self, stream_id: str) -> str:
        return os.path.join(self.streams_dir, f"{stream_id}.jsonl")

    def exists_on_disk(self, stream_id: str) -> bool:
        return os.path.exists(self.path_for(stream_id))

    def cleanup_old(self, retention_days: int) -> int:
        """Delete <id>.jsonl files older than `retention_days`. Returns count."""
        cutoff = time.time() - (retention_days * 86400)
        removed = 0
        try:
            for name in os.listdir(self.streams_dir):
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(self.streams_dir, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError:
                    continue
        except FileNotFoundError:
            pass
        return removed


def replay_events_from_disk(path: str):
    """Yield (event, seq) pairs from a stream file, stopping at END_MARKER.

    Lives as a free function (not a Stream method) because callers reading
    from disk-only — after a backend restart wipes the in-memory registry —
    have no Stream object to call into.
    """
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            seq = event.get("_seq", 0)
            if event.get("type") == END_MARKER:
                yield event, seq
                return
            yield event, seq
