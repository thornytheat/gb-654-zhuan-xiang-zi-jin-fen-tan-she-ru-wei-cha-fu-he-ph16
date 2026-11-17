from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    digest TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_versions (
    version_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL UNIQUE,
    digest TEXT NOT NULL,
    parent_version_id TEXT,
    rule_version TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    version_id TEXT NOT NULL,
    parent_batch_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_heads (
    scope TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    batch_id TEXT NOT NULL UNIQUE,
    version_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    result TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self):
        return sqlite3.connect(self.path)

    @contextmanager
    def transaction(self):
        """写事务：任何异常都回滚，已保存的旧批次保持原样。"""
        db = self.connect()
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    # ---- 证据批次（既有语义） -------------------------------------------
    def record(self, digest: str, run_id: str, result: dict) -> dict:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                       (digest, run_id, json.dumps(result, ensure_ascii=False, sort_keys=True)))
            row = db.execute("SELECT run_id, result FROM runs WHERE digest=?", (digest,)).fetchone()
        return {"run_id": row[0], "result": json.loads(row[1])}

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def all(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT run_id, digest, result FROM runs ORDER BY run_id").fetchall()
        return [{"run_id": r[0], "digest": r[1], "result": json.loads(r[2])} for r in rows]

    # ---- 采信版本（不可变） ---------------------------------------------
    @staticmethod
    def _version(row: sqlite3.Row) -> dict:
        return {
            "version_id": row["version_id"],
            "seq": row["seq"],
            "digest": row["digest"],
            "parent_version_id": row["parent_version_id"],
            "rule_version": row["rule_version"],
            "note": row["note"],
            "created_at": row["created_at"],
            "snapshot": json.loads(row["snapshot"]),
        }

    def latest_version(self) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM budget_versions ORDER BY seq DESC LIMIT 1").fetchone()
        return self._version(row) if row else None

    def version(self, version_id: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM budget_versions WHERE version_id=?", (version_id,)).fetchone()
        return self._version(row) if row else None

    def version_by_digest(self, digest: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM budget_versions WHERE digest=? ORDER BY seq DESC LIMIT 1", (digest,)
            ).fetchone()
        return self._version(row) if row else None

    def versions(self) -> list[dict]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM budget_versions ORDER BY seq").fetchall()
        values = [self._version(row) for row in rows]
        latest = values[-1]["version_id"] if values else None
        for value in values:
            value["status"] = "current" if value["version_id"] == latest else "superseded"
            value["is_current"] = value["version_id"] == latest
        return values

    def save_version(self, snapshot: dict, digest: str, rule_version: str,
                     parent_version_id: str | None = None, note: str | None = None) -> tuple[dict, bool]:
        """追加一个不可变采信版本。

        与当前最新版本摘要相同的重试复用原版本，不重复入账；相同摘要若来自
        更早版本，则作为新提交追加，历史版本仍按原样保留。
        """
        with self.transaction() as db:
            latest = db.execute("SELECT * FROM budget_versions ORDER BY seq DESC LIMIT 1").fetchone()
            if latest is not None and latest["digest"] == digest:
                return self._version(latest), True
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM budget_versions").fetchone()[0]
            if parent_version_id is None:
                parent_version_id = latest["version_id"] if latest else None
            version_id = "ver-" + digest[:12]
            if db.execute("SELECT 1 FROM budget_versions WHERE version_id=?", (version_id,)).fetchone():
                version_id = f"ver-{digest[:10]}-{seq}"
            db.execute(
                "INSERT INTO budget_versions (version_id, seq, digest, parent_version_id, rule_version, note, created_at, snapshot)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (version_id, seq, digest, parent_version_id, rule_version, note, _now(),
                 json.dumps(snapshot, ensure_ascii=False, sort_keys=True)),
            )
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM budget_versions WHERE version_id=?", (version_id,)).fetchone()
            return self._version(row), False

    # ---- 分摊批次 --------------------------------------------------------
    @staticmethod
    def _batch(row: sqlite3.Row, head_id: str | None) -> dict:
        return {
            "batch_id": row["batch_id"],
            "seq": row["seq"],
            "scope": row["scope"],
            "version_id": row["version_id"],
            "parent_batch_id": row["parent_batch_id"],
            "idempotency_key": row["idempotency_key"],
            "created_at": row["created_at"],
            "status": "active" if row["batch_id"] == head_id else "superseded",
            "is_current": row["batch_id"] == head_id,
            "result": json.loads(row["result"]),
        }

    def _head_id(self, db: sqlite3.Connection, scope: str) -> str | None:
        row = db.execute("SELECT batch_id FROM batch_heads WHERE scope=?", (scope,)).fetchone()
        return row[0] if row else None

    def head_batch(self, scope: str = "default") -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            head = self._head_id(db, scope)
            if not head:
                return None
            row = db.execute("SELECT * FROM batches WHERE batch_id=?", (head,)).fetchone()
        return self._batch(row, head) if row else None

    def batch(self, batch_id: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not row:
                return None
            head = self._head_id(db, row["scope"])
        return self._batch(row, head)

    def batch_by_key(self, idempotency_key: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM batches WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if not row:
                return None
            head = self._head_id(db, row["scope"])
        return self._batch(row, head)

    def batches(self, scope: str | None = None) -> list[dict]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            if scope is None:
                rows = db.execute("SELECT * FROM batches ORDER BY seq").fetchall()
            else:
                rows = db.execute("SELECT * FROM batches WHERE scope=? ORDER BY seq", (scope,)).fetchall()
            heads = {row["scope"]: row["batch_id"] for row in
                     db.execute("SELECT scope, batch_id FROM batch_heads").fetchall()}
        return [self._batch(row, heads.get(row["scope"])) for row in rows]

    def save_batch(self, scope: str, version_id: str, idempotency_key: str, result: dict,
                   parent_batch_id: str | None = None) -> tuple[dict, bool]:
        """保存批次并切换当前批次。

        同一幂等键重试时返回原批次并把指针指回该已保存位置，不新增行、
        不改写历史结果；任何写入异常整体回滚，旧批次保持原样。
        """
        with self.transaction() as db:
            row = db.execute("SELECT * FROM batches WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if row:
                db.execute(
                    "INSERT INTO batch_heads (scope, batch_id, updated_at) VALUES (?, ?, ?)"
                    " ON CONFLICT(scope) DO UPDATE SET batch_id=excluded.batch_id, updated_at=excluded.updated_at",
                    (row["scope"], row["batch_id"], _now()),
                )
                return self._batch(row, row["batch_id"]), True
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM batches").fetchone()[0]
            head_before = self._head_id(db, scope)
            parent = parent_batch_id if parent_batch_id is not None else head_before
            batch_id = "batch-" + idempotency_key[:12]
            if db.execute("SELECT 1 FROM batches WHERE batch_id=?", (batch_id,)).fetchone():
                batch_id = f"batch-{idempotency_key[:10]}-{seq}"
            db.execute(
                "INSERT INTO batches (batch_id, seq, scope, version_id, parent_batch_id, idempotency_key,"
                " status, created_at, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, seq, scope, version_id, parent, idempotency_key, "recorded", _now(),
                 json.dumps(result, ensure_ascii=False, sort_keys=True)),
            )
            db.execute(
                "INSERT INTO batch_heads (scope, batch_id, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(scope) DO UPDATE SET batch_id=excluded.batch_id, updated_at=excluded.updated_at",
                (scope, batch_id, _now()),
            )
            row = db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        return self._batch(row, batch_id), False

    def restore_batch(self, batch_id: str) -> dict | None:
        """把当前批次指回已保存位置；不新增批次、不改写历史结果。"""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not row:
                return None
            scope = row["scope"]
            previous = self._head_id(db, scope)
            db.execute(
                "INSERT INTO batch_heads (scope, batch_id, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(scope) DO UPDATE SET batch_id=excluded.batch_id, updated_at=excluded.updated_at",
                (scope, batch_id, _now()),
            )
            row = db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        value = self._batch(row, batch_id)
        value["previous_batch_id"] = previous
        return value

    # ---- 尾差复核（绑定批次与当时采信版本） -------------------------------
    @staticmethod
    def _review(row: sqlite3.Row) -> dict:
        return {
            "review_id": row["review_id"],
            "scope": row["scope"],
            "batch_id": row["batch_id"],
            "version_id": row["version_id"],
            "created_at": row["created_at"],
            "result": json.loads(row["result"]),
        }

    def review(self, review_id: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
        return self._review(row) if row else None

    def review_for_batch(self, batch_id: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM reviews WHERE batch_id=?", (batch_id,)).fetchone()
        return self._review(row) if row else None

    def reviews(self, scope: str | None = None) -> list[dict]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            if scope is None:
                rows = db.execute("SELECT * FROM reviews ORDER BY created_at, review_id").fetchall()
            else:
                rows = db.execute("SELECT * FROM reviews WHERE scope=? ORDER BY created_at, review_id", (scope,)).fetchall()
        return [self._review(row) for row in rows]

    def save_review(self, scope: str, batch_id: str, version_id: str, review_id: str,
                    result: dict) -> tuple[dict, bool]:
        """按批次唯一保存复核记录；重复出具返回首份记录，不重复入账。"""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM reviews WHERE batch_id=?", (batch_id,)).fetchone()
            if row:
                return self._review(row), True
            db.execute(
                "INSERT INTO reviews (review_id, scope, batch_id, version_id, created_at, result)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (review_id, scope, batch_id, version_id, _now(),
                 json.dumps(result, ensure_ascii=False, sort_keys=True)),
            )
            row = db.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
        return self._review(row), False
