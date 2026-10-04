"""Thread-safe persistent local identities, sessions and bounded telemetry records."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .product_models import ConnectionCreate, ConnectionUpdate, TelemetryBatch

LIMITS = {
    "body_bytes": 262144,
    "event_bytes": 49152,
    "events_per_batch": 100,
    "events_per_minute": 600,
    "entities_per_snapshot": 128,
    "connections": 50,
    "sessions": 200,
    "events_per_session": 20000,
    "total_events": 100000,
}


def utc_now():
    return datetime.now(UTC).isoformat()


def canonical(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


class ProductError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class ProductStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "product.sqlite3"
        self.lock = threading.RLock()
        self.rates = defaultdict(deque)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS connections(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,adapter TEXT NOT NULL,
              description TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,last_seen_at TEXT,
              deleted INTEGER NOT NULL DEFAULT 0,event_count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(
              id TEXT PRIMARY KEY,connection_id TEXT NOT NULL,external_id TEXT NOT NULL,
              name TEXT NOT NULL,status TEXT NOT NULL,source_kind TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,last_sequence INTEGER NOT NULL DEFAULT -1,
              last_source_time REAL NOT NULL DEFAULT 0,event_count INTEGER NOT NULL DEFAULT 0,
              run_path TEXT,replay_json TEXT,UNIQUE(connection_id,external_id));
            CREATE TABLE IF NOT EXISTS events(
              id TEXT PRIMARY KEY,session_id TEXT NOT NULL,sequence INTEGER NOT NULL,
              source_time REAL NOT NULL,received_at TEXT NOT NULL,type TEXT NOT NULL,
              payload TEXT NOT NULL,
              fingerprint TEXT NOT NULL,UNIQUE(session_id,sequence));
            CREATE INDEX IF NOT EXISTS events_session_sequence ON events(session_id,sequence);
            """)
            if "replay_json" not in {row[1] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN replay_json TEXT")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def connection_row(row):
        result = {
            key: row[key]
            for key in (
                "id",
                "name",
                "adapter",
                "description",
                "created_at",
                "last_seen_at",
                "event_count",
            )
        }
        recent = (
            row["last_seen_at"]
            and (datetime.now(UTC) - datetime.fromisoformat(row["last_seen_at"])).total_seconds()
            <= 30
        )
        result["status"] = (
            "revoked"
            if row["deleted"]
            else (
                "connected" if recent else "stale" if row["last_seen_at"] else "awaiting_telemetry"
            )
        )
        return result

    def create_connection(self, data: ConnectionCreate):
        token, connection_id = secrets.token_urlsafe(32), uuid.uuid4().hex
        with self.lock, self.connect() as db:
            if (
                db.execute("SELECT COUNT(*) FROM connections WHERE deleted=0").fetchone()[0]
                >= LIMITS["connections"]
            ):
                raise ProductError("retention_limit", "Maximum local connections reached", 409)
            db.execute(
                "INSERT INTO connections(id,name,adapter,description,token_hash,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    connection_id,
                    data.name,
                    data.adapter,
                    data.description,
                    hashlib.sha256(token.encode()).hexdigest(),
                    utc_now(),
                ),
            )
            row = db.execute("SELECT * FROM connections WHERE id=?", (connection_id,)).fetchone()
        return self.connection_row(row), token

    def connections(self):
        with self.connect() as db:
            return [
                self.connection_row(row)
                for row in db.execute(
                    "SELECT * FROM connections WHERE deleted=0 ORDER BY created_at DESC"
                )
            ]

    def connection(self, connection_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM connections WHERE id=? AND deleted=0", (connection_id,)
            ).fetchone()
        if row is None:
            raise ProductError("not_found", "Connection not found", 404)
        return self.connection_row(row)

    def update_connection(self, connection_id, data: ConnectionUpdate):
        self.connection(connection_id)
        fields = data.model_dump(exclude_none=True)
        with self.lock, self.connect() as db:
            for field, value in fields.items():
                db.execute(
                    f"UPDATE connections SET {field}=? WHERE id=? AND deleted=0",
                    (value, connection_id),
                )
        return self.connection(connection_id)

    def delete_connection(self, connection_id):
        self.connection(connection_id)
        with self.lock, self.connect() as db:
            db.execute("UPDATE connections SET deleted=1 WHERE id=?", (connection_id,))
        return {"deleted": True, "id": connection_id, "sessions_preserved": True}

    def authorize(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise ProductError("unauthorized", "A valid integration bearer token is required", 401)
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM connections WHERE token_hash=? AND deleted=0",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        if row is None:
            raise ProductError("unauthorized", "A valid integration bearer token is required", 401)
        return row[0]

    def session(self, session_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT s.*,c.name AS connection_name FROM sessions s "
                "LEFT JOIN connections c ON s.connection_id=c.id WHERE s.id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise ProductError("not_found", "Session not found", 404)
        result = dict(row)
        result.pop("run_path", None)
        result.pop("replay_json", None)
        result["scope"] = "synthetic_simulation"
        return result

    def sessions(self):
        with self.connect() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT ?",
                    (LIMITS["sessions"],),
                )
            ]
        return [self.session(session_id) for session_id in ids]

    def create_session(
        self, connection_id, external_id, name, source_kind="integration", run_path=None
    ):
        with self.lock, self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= LIMITS["sessions"]:
                raise ProductError("retention_limit", "Maximum retained sessions reached", 409)
            session_id, now = uuid.uuid4().hex, utc_now()
            db.execute(
                "INSERT INTO sessions(id,connection_id,external_id,name,status,source_kind,"
                "created_at,updated_at,run_path) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    connection_id,
                    external_id,
                    name,
                    "running",
                    source_kind,
                    now,
                    now,
                    str(run_path) if run_path else None,
                ),
            )
        return self.session(session_id)

    def ingest(
        self,
        connection_id,
        batch: TelemetryBatch,
        *,
        rate_limit=True,
        source_kind="integration",
        allow_late=False,
    ):
        encoded = [(event, canonical(event.model_dump())) for event in batch.events]
        if any(len(text.encode()) > LIMITS["event_bytes"] for _, text in encoded):
            raise ProductError("event_too_large", "An event exceeds the local size limit", 413)
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            connection = db.execute(
                "SELECT deleted FROM connections WHERE id=?", (connection_id,)
            ).fetchone()
            if connection is None or connection[0]:
                raise ProductError("unauthorized", "Connection has been revoked", 401)
            row = db.execute(
                "SELECT * FROM sessions WHERE connection_id=? AND external_id=?",
                (connection_id, batch.session.external_id),
            ).fetchone()
            if row is None:
                if db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= LIMITS["sessions"]:
                    raise ProductError("retention_limit", "Maximum retained sessions reached", 409)
                session_id, now = uuid.uuid4().hex, utc_now()
                db.execute(
                    "INSERT INTO sessions(id,connection_id,external_id,name,status,source_kind,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        session_id,
                        connection_id,
                        batch.session.external_id,
                        batch.session.name,
                        "running",
                        source_kind,
                        now,
                        now,
                    ),
                )
                row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            session_id = row["id"]
            sequence = row["last_sequence"]
            source_time = row["last_source_time"]
            new = []
            duplicates = 0
            for event, text in encoded:
                fingerprint = hashlib.sha256(text.encode()).hexdigest()
                previous = db.execute(
                    "SELECT fingerprint FROM events WHERE session_id=? AND sequence=?",
                    (session_id, event.sequence),
                ).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        raise ProductError(
                            "sequence_conflict",
                            "A sequence was reused with different event data",
                            409,
                        )
                    duplicates += 1
                    continue
                if event.sequence <= sequence or (
                    event.source_time < source_time and not allow_late
                ):
                    raise ProductError(
                        "out_of_order",
                        "New events require increasing sequence and nondecreasing source time",
                        409,
                    )
                if row["status"] != "running":
                    raise ProductError(
                        "session_closed", "Start a new external session ID for a new run", 409
                    )
                sequence, source_time = event.sequence, max(source_time, event.source_time)
                new.append((event, fingerprint))
            session_count = db.execute(
                "SELECT COUNT(*) FROM events WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            total_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            if (
                session_count + len(new) > LIMITS["events_per_session"]
                or total_count + len(new) > LIMITS["total_events"]
            ):
                raise ProductError(
                    "retention_limit",
                    "Storage capacity reached; accepted records have been preserved",
                    409,
                )
            if any(event.type == "assessment" and event.payload["hypotheses"] for event, _ in new):
                public_ids = {}
                for existing in db.execute(
                    "SELECT source_time,payload FROM events WHERE session_id=? AND type='evidence'",
                    (session_id,),
                ):
                    for record in json.loads(existing["payload"])["records"]:
                        public_ids[record["id"]] = min(
                            public_ids.get(record["id"], float("inf")), existing["source_time"]
                        )
                for event, _ in new:
                    if event.type == "evidence":
                        for record in event.payload["records"]:
                            public_ids[record["id"]] = min(
                                public_ids.get(record["id"], float("inf")), event.source_time
                            )
                    elif event.type == "assessment":
                        if any(
                            public_ids.get(source, float("inf")) > event.source_time
                            for hypothesis in event.payload["hypotheses"]
                            for source in hypothesis["evidence_ids"]
                        ):
                            raise ProductError(
                                "unknown_evidence",
                                "Assessment references must identify previously received "
                                "public evidence",
                                422,
                            )
            now_monotonic = time.monotonic()
            rate = self.rates[connection_id]
            while rate and rate[0][0] < now_monotonic - 60:
                rate.popleft()
            if rate_limit and sum(n for _, n in rate) + len(new) > LIMITS["events_per_minute"]:
                raise ProductError("rate_limit", "Connection exceeded 600 events per minute", 429)
            now = utc_now()
            for event, fingerprint in new:
                db.execute(
                    "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        session_id,
                        event.sequence,
                        event.source_time,
                        now,
                        event.type,
                        canonical(event.payload),
                        fingerprint,
                    ),
                )
            if new:
                rate.append((now_monotonic, len(new)))
                db.execute(
                    "UPDATE connections SET last_seen_at=?,event_count=event_count+? WHERE id=?",
                    (now, len(new), connection_id),
                )
            if new:
                db.execute(
                    "UPDATE sessions SET name=?,status=?,updated_at=?,last_sequence=?,"
                    "last_source_time=?,event_count=event_count+? WHERE id=?",
                    (
                        batch.session.name,
                        batch.session.status,
                        now,
                        sequence,
                        source_time,
                        len(new),
                        session_id,
                    ),
                )
        return {
            "session": self.session(session_id),
            "accepted_events": len(new),
            "duplicate_events": duplicates,
        }

    def events(self, session_id, after=-1, limit=200, *, tail=False):
        self.session(session_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE session_id=? AND sequence>? ORDER BY sequence "
                + ("DESC" if tail else "ASC")
                + " LIMIT ?",
                (session_id, after, min(limit, 2000)),
            ).fetchall()
        result = [
            {
                **{
                    k: row[k]
                    for k in ("id", "session_id", "sequence", "source_time", "received_at", "type")
                },
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]
        return sorted(result, key=lambda item: item["sequence"])

    def set_session_status(self, session_id, status):
        with self.lock, self.connect() as db:
            db.execute(
                "UPDATE sessions SET status=?,updated_at=? WHERE id=?",
                (status, utc_now(), session_id),
            )

    def run_path(self, session_id):
        self.session(session_id)
        with self.connect() as db:
            return db.execute("SELECT run_path FROM sessions WHERE id=?", (session_id,)).fetchone()[
                0
            ]

    def set_replay(self, session_id, metadata):
        with self.lock, self.connect() as db:
            db.execute(
                "UPDATE sessions SET replay_json=? WHERE id=?", (canonical(metadata), session_id)
            )

    def replay_metadata(self, session_id):
        self.session(session_id)
        with self.connect() as db:
            raw = db.execute(
                "SELECT replay_json FROM sessions WHERE id=?", (session_id,)
            ).fetchone()[0]
        return json.loads(raw) if raw else {}
