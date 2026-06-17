"""SQLite-backed pipeline state store.

Replaces the previous "rewrite ~/qa-dashboard/pipeline-state.json on every
update" scheme. That design had two problems:

  1. Lost-update race: two coroutines reading the same in-memory dict,
     each computing a new value, each calling json.dump — the second
     write clobbered the first.
  2. Torn writes: a crash between open(..., 'w') and json.dump landing
     truncated the entire history.

SQLite fixes both: a single UPSERT is atomic, and WAL mode handles
concurrent readers cleanly. The in-memory dict survives as a read cache
because env-lock checks happen on every pipeline POST and on the 10s
polling cycle — going to SQLite for every read is unnecessary latency
when the canonical state is always also in memory.

Atomicity invariant: every state mutation goes through `upsert()`, which
holds a single lock for the read-merge-write-cache cycle. Two concurrent
upserts on the same pipeline_id serialize cleanly; the second one sees
the first one's merged state.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional


# Columns recorded for documentation — kept in sync with the SQLite schema below.
# Note: write filtering is not enforced; arbitrary keys persist in the in-memory
# cache (see test_concurrent_upserts_on_same_id_dont_lose_updates), and only the
# columns named here round-trip through SQLite via _write_row().
_COLUMN_FIELDS = (
    "ticketKey", "env", "repo", "branch", "service",
    "snapshot", "envUrl", "stage", "status",
    "councilStatus", "councilPayload", "councilOverride",
)


def _maybe_json(raw):
    """Decode `raw` as JSON, tolerating None / empty / non-JSON strings.

    Returns None for None/empty; returns the parsed value if it round-trips
    cleanly; otherwise returns the raw string unchanged. Used for the council
    columns which store dict/list payloads that we want surfaced as objects
    in callers' state dicts."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


class PipelineStore:
    """Durable + in-memory pipeline state. Single instance per process."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # check_same_thread=False so async handlers across threads can share it.
        # The single lock below provides the serialization SQLite needs.
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self._init_schema()
        self._cache: dict[str, dict] = self._load_cache()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pipelines (
                pipeline_id      TEXT PRIMARY KEY,
                ticket_key       TEXT NOT NULL DEFAULT '',
                env              TEXT NOT NULL DEFAULT '',
                repo             TEXT NOT NULL DEFAULT '',
                branch           TEXT NOT NULL DEFAULT '',
                service          TEXT NOT NULL DEFAULT '',
                snapshot         TEXT NOT NULL DEFAULT '',
                env_url          TEXT NOT NULL DEFAULT '',
                stage            TEXT NOT NULL DEFAULT 'builder',
                status           TEXT NOT NULL DEFAULT 'running',
                logs_json        TEXT NOT NULL DEFAULT '[]',
                updated_at       REAL NOT NULL,
                council_status   TEXT,
                council_payload  TEXT,
                council_override TEXT
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_pipelines_status ON pipelines(status)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_pipelines_updated ON pipelines(updated_at)")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_meta (
              key   TEXT PRIMARY KEY,
              value TEXT NOT NULL
            )
            """
        )
        # Idempotent migration for DBs that pre-date the council columns.
        existing_cols = {
            row[1] for row in self._conn.execute("PRAGMA table_info(pipelines)").fetchall()
        }
        for col in ("council_status", "council_payload", "council_override"):
            if col not in existing_cols:
                self._conn.execute(f"ALTER TABLE pipelines ADD COLUMN {col} TEXT")

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> dict:
        try:
            logs = json.loads(row["logs_json"])
            if not isinstance(logs, list):
                logs = []
        except (json.JSONDecodeError, TypeError):
            logs = []
        # Defensively pull council_* columns. PRAGMA table_info-based migration
        # runs in _init_schema, but row factory is bound per-connection, so
        # tolerate the brief window where a row was read before the columns
        # showed up. row.keys() lists available columns for sqlite3.Row.
        available = set(row.keys())

        def _col(name):
            return row[name] if name in available else None

        return {
            "ticketKey": row["ticket_key"],
            "env": row["env"],
            "repo": row["repo"],
            "branch": row["branch"],
            "service": row["service"],
            "snapshot": row["snapshot"],
            "envUrl": row["env_url"],
            "stage": row["stage"],
            "status": row["status"],
            "logs": logs,
            "updated_at": row["updated_at"],
            "councilStatus": _col("council_status"),
            "councilPayload": _maybe_json(_col("council_payload")),
            "councilOverride": _maybe_json(_col("council_override")),
        }

    def _load_cache(self) -> dict[str, dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM pipelines")
        out = {}
        for row in cur:
            out[row["pipeline_id"]] = self._row_to_state(row)
        return out

    # ── Public API ──────────────────────────────────────────────────────

    def all_states(self) -> dict[str, dict]:
        """Return a shallow copy of the cache. Callers can iterate freely."""
        return dict(self._cache)

    def get(self, pipeline_id: str) -> Optional[dict]:
        return self._cache.get(pipeline_id)

    def upsert(self, pipeline_id: str, updates: dict) -> dict:
        """Merge `updates` into the pipeline's state. Single-statement atomic.

        Returns the post-merge state dict. The lock holds for the entire
        read-merge-write cycle, so concurrent upserts on the same id can't
        lose each other's writes.
        """
        with self._lock:
            current = self._cache.get(pipeline_id, {}).copy()
            current.update(updates)
            current["updated_at"] = time.time()
            self._cache[pipeline_id] = current
            self._write_row(pipeline_id, current)
            return current

    def remove(self, pipeline_id: str) -> None:
        with self._lock:
            self._cache.pop(pipeline_id, None)
            self._conn.execute("DELETE FROM pipelines WHERE pipeline_id = ?", (pipeline_id,))

    def cleanup_old(self, retention_days: int) -> int:
        """Drop completed/failed pipelines older than retention_days. Returns count."""
        cutoff = time.time() - (retention_days * 86400)
        with self._lock:
            cur = self._conn.execute(
                "SELECT pipeline_id FROM pipelines WHERE status IN ('completed','failed') AND updated_at < ?",
                (cutoff,),
            )
            ids = [row[0] for row in cur.fetchall()]
            for pid in ids:
                self._cache.pop(pid, None)
            if ids:
                placeholders = ",".join("?" * len(ids))
                self._conn.execute(
                    f"DELETE FROM pipelines WHERE pipeline_id IN ({placeholders})",
                    ids,
                )
            return len(ids)

    def get_meta(self, key: str, default: Any = None) -> Any:
        """Return JSON-decoded value for `key`, or `default` if missing/undecodable.

        Note: storing None means get_meta returns None (not default).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM pipeline_meta WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return default

    def set_meta(self, key: str, value: Any) -> None:
        """UPSERT `value` (JSON-encoded) under `key` in the pipeline_meta table."""
        encoded = json.dumps(value)
        with self._lock:
            self._conn.execute(
                "INSERT INTO pipeline_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, encoded),
            )

    # ── Internal ────────────────────────────────────────────────────────

    def _write_row(self, pipeline_id: str, state: dict) -> None:
        """Persist `state` to SQLite via UPSERT. Caller holds self._lock."""
        logs = state.get("logs", [])
        if not isinstance(logs, list):
            logs = []
        logs_json = json.dumps(logs[-50:])

        # Council fields: status is a plain string (e.g. "pending"/"block"/"approve"),
        # payload + override are dict/list structures that must JSON-encode before
        # hitting the TEXT column. Pass None through so missing fields stay NULL.
        def _encode(value):
            if value is None:
                return None
            if isinstance(value, (dict, list)):
                return json.dumps(value)
            return value

        council_status = state.get("councilStatus")
        council_payload = _encode(state.get("councilPayload"))
        council_override = _encode(state.get("councilOverride"))

        self._conn.execute(
            """
            INSERT INTO pipelines (
                pipeline_id, ticket_key, env, repo, branch, service,
                snapshot, env_url, stage, status, logs_json, updated_at,
                council_status, council_payload, council_override
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pipeline_id) DO UPDATE SET
                ticket_key       = excluded.ticket_key,
                env              = excluded.env,
                repo             = excluded.repo,
                branch           = excluded.branch,
                service          = excluded.service,
                snapshot         = excluded.snapshot,
                env_url          = excluded.env_url,
                stage            = excluded.stage,
                status           = excluded.status,
                logs_json        = excluded.logs_json,
                updated_at       = excluded.updated_at,
                council_status   = excluded.council_status,
                council_payload  = excluded.council_payload,
                council_override = excluded.council_override
            """,
            (
                pipeline_id,
                state.get("ticketKey", ""),
                state.get("env", ""),
                state.get("repo", ""),
                state.get("branch", ""),
                state.get("service", ""),
                state.get("snapshot", ""),
                state.get("envUrl", ""),
                state.get("stage", "builder"),
                state.get("status", "running"),
                logs_json,
                state.get("updated_at") or time.time(),
                council_status,
                council_payload,
                council_override,
            ),
        )

    # ── Migration ────────────────────────────────────────────────────────

    def migrate_from_json(self, json_path: str) -> int:
        """One-shot import from the legacy pipeline-state.json. Idempotent —
        only imports if the cache is currently empty. Renames the source file
        to .migrated so a second startup doesn't reprocess it."""
        if self._cache or not os.path.exists(json_path):
            return 0
        try:
            with open(json_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return 0
        if not isinstance(data, dict):
            return 0
        count = 0
        for pid, state in data.items():
            if not isinstance(state, dict):
                continue
            self.upsert(pid, state)
            count += 1
        if count > 0:
            try:
                os.rename(json_path, json_path + ".migrated")
            except OSError:
                pass
        return count
