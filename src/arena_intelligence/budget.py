"""Cross-process, persistent API budget. Amounts are integer nanodollars.

Pricing source: https://platform.claude.com/docs/en/models/sonnet-5-5/overview
Verified 2026-10-04: $2/M input, $10/M output. No prompt caching is requested.
Interrupted requests retain their worst-case charge until reconciled; restarting
the program cannot erase outstanding spend.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

Bucket = Literal["dev", "eval", "demo"]
NANO_PER_USD = 1_000_000_000
INPUT_NANO_PER_TOKEN = 2000
OUTPUT_NANO_PER_TOKEN = 10000
BUCKET_LIMITS = {"dev": 10 * NANO_PER_USD, "eval": 35 * NANO_PER_USD, "demo": 10 * NANO_PER_USD}
GLOBAL_LIMIT = 55 * NANO_PER_USD


class BudgetExceeded(RuntimeError):
    pass


class CostLedger:
    def __init__(self, path: Path):
        configured = os.environ.get("ARENA_PROJECT_CEILING_USD", "55")
        try:
            ceiling = Decimal(configured)
        except InvalidOperation as error:
            raise ValueError("ARENA_PROJECT_CEILING_USD must be a decimal in (0,55]") from error
        if not ceiling.is_finite() or not Decimal(0) < ceiling <= Decimal(55):
            raise ValueError("ARENA_PROJECT_CEILING_USD must be a decimal in (0,55]")
        ceiling_nano = int(ceiling * NANO_PER_USD)
        if ceiling_nano <= 0:
            raise ValueError("project ceiling must be at least one nanodollar")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, bucket TEXT NOT NULL, run_id TEXT NOT NULL,
                stage TEXT NOT NULL, reserved_nano INTEGER NOT NULL,
                charged_nano INTEGER, status TEXT NOT NULL, created REAL NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER)"""
            )

            db.execute(
                "CREATE TABLE IF NOT EXISTS configuration "
                "(key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO configuration VALUES ('project_ceiling_nano',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=MIN(value,excluded.value)",
                    (ceiling_nano,),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    def reserve(self, bucket: Bucket, run_id: str, stage: str, amount_nano: int) -> str:
        if bucket not in BUCKET_LIMITS or amount_nano <= 0:
            raise ValueError("invalid reservation")
        request_id = uuid.uuid4().hex
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                total = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(charged_nano,reserved_nano)),0) FROM requests"
                ).fetchone()[0]
                bucket_total = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(charged_nano,reserved_nano)),0) "
                    "FROM requests WHERE bucket=?",
                    (bucket,),
                ).fetchone()[0]
                ceiling = min(
                    GLOBAL_LIMIT,
                    db.execute(
                        "SELECT value FROM configuration WHERE key='project_ceiling_nano'"
                    ).fetchone()[0],
                )
                if total + amount_nano > ceiling:
                    raise BudgetExceeded(
                        f"${ceiling / NANO_PER_USD:g} project API budget would be exceeded"
                    )
                if bucket_total + amount_nano > BUCKET_LIMITS[bucket]:
                    raise BudgetExceeded(f"{bucket} API budget would be exceeded")
                db.execute(
                    "INSERT INTO requests VALUES (?,?,?,?,?,NULL,'reserved',?,NULL,NULL)",
                    (request_id, bucket, run_id, stage, amount_nano, time.time()),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return request_id

    def settle(self, request_id: str, input_tokens: int, output_tokens: int) -> float:
        if min(input_tokens, output_tokens) < 0:
            raise ValueError("negative usage")
        amount = input_tokens * INPUT_NANO_PER_TOKEN + output_tokens * OUTPUT_NANO_PER_TOKEN
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,reserved_nano FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if row is None or row[0] != "reserved":
                db.execute("ROLLBACK")
                raise ValueError("reservation missing or already settled")
            db.execute(
                "UPDATE requests SET charged_nano=?,status='settled',"
                "input_tokens=?,output_tokens=? WHERE id=?",
                (amount, input_tokens, output_tokens, request_id),
            )
            db.execute("COMMIT")
        return amount / NANO_PER_USD

    def mark_uncertain(self, request_id: str) -> None:
        """Retain reservation after cancellation, timeout, or ambiguous transport failure."""
        with self._connection() as db:
            db.execute(
                "UPDATE requests SET status='uncertain' WHERE id=? AND status='reserved'",
                (request_id,),
            )

    def summary(self) -> dict:
        with self._connection() as db:
            rows = db.execute(
                "SELECT bucket,SUM(COALESCE(charged_nano,reserved_nano)),"
                "SUM(status!='settled'),COUNT(*) FROM requests GROUP BY bucket"
            ).fetchall()
            ceiling = db.execute(
                "SELECT value FROM configuration WHERE key='project_ceiling_nano'"
            ).fetchone()[0]
        amounts = {b: 0 for b in BUCKET_LIMITS}
        pending, requests = 0, 0
        for bucket, amount, n_pending, count in rows:
            amounts[bucket] = amount
            pending += n_pending
            requests += count
        return {
            "charged_or_reserved_usd": sum(amounts.values()) / NANO_PER_USD,
            "ceiling_usd": ceiling / NANO_PER_USD,
            "buckets": {
                b: {"used_usd": v / NANO_PER_USD, "limit_usd": BUCKET_LIMITS[b] / NANO_PER_USD}
                for b, v in amounts.items()
            },
            "unreconciled_requests": pending,
            "requests": requests,
        }


def worst_case_cost(prompt_bytes: int, schema_bytes: int, max_output: int) -> int:
    # Reserve the full published 1M-token context instead of guessing schema overhead.
    # Successful requests settle to actual usage; interrupted requests retain headroom.
    if prompt_bytes + schema_bytes > 160_000:
        raise ValueError("reasoning input exceeds the local bounded-input limit")
    return 1_000_000 * INPUT_NANO_PER_TOKEN + max_output * OUTPUT_NANO_PER_TOKEN
