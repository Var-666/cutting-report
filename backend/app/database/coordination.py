from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from time import time
from uuid import uuid4

from app.database.sqlite import SQLiteRepository, ExecutionLeaseLost


logger = logging.getLogger(__name__)


def backup_before_process_upgrade(database) -> None:
    if not database.path.is_file():
        return
    with database.connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "report_jobs" not in tables or "process_leases" in tables:
            return
        directory = database.path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{database.path.stem}-before-processes-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.db"
        destination = sqlite3.connect(target)
        try:
            connection.backup(destination)
        finally:
            destination.close()
        os.chmod(target, 0o600)


class CoordinationRepository(SQLiteRepository):
    @staticmethod
    def _require_owner(connection, owner):
        if connection.execute("SELECT 1 FROM process_leases WHERE name='worker' AND owner=? AND expires_at>?",
                              (owner, time())).fetchone() is None:
            raise ExecutionLeaseLost("后台执行租约已失效")

    def initialize(self) -> None:
        from app.database.migrations import apply_migrations
        apply_migrations(self.database, names=("001_coordination",))

    def acquire(self, name: str, *, ttl: float) -> str | None:
        owner = uuid4().hex
        with self.connect() as connection:
            self.begin_immediate(connection)
            now = time()
            updated = connection.execute("""
                INSERT INTO process_leases(name, owner, expires_at, heartbeat_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET
                    owner=excluded.owner, expires_at=excluded.expires_at,
                    heartbeat_at=excluded.heartbeat_at, status_json='{}'
                WHERE process_leases.expires_at<=?
            """, (name, owner, now + ttl, now, now))
            return owner if updated.rowcount == 1 else None

    def renew(self, name: str, owner: str, *, ttl: float, status: dict | None = None) -> bool:
        with self.connect() as connection:
            self.begin_immediate(connection)
            now = time()
            updated = connection.execute("""
                UPDATE process_leases SET expires_at=?, heartbeat_at=?,
                    status_json=COALESCE(?, status_json)
                WHERE name=? AND owner=? AND expires_at>?
            """, (now + ttl, now, json.dumps(status) if status is not None else None, name, owner, now))
            return updated.rowcount == 1

    def release(self, name: str, owner: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE process_leases SET expires_at=0 WHERE name=? AND owner=?", (name, owner))

    def worker_status(self) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM process_leases WHERE name='worker'").fetchone()
        if row is None:
            return {"state": "missing", "report_alive": False, "stalled_workers": []}
        status = json.loads(row["status_json"])
        age = max(0, time() - row["heartbeat_at"])
        state = "stopped" if row["expires_at"] == 0 else "lost" if row["expires_at"] <= time() else "running"
        if state == "running" and status.get("stalled_workers"):
            state = "stalled"
        if state in {"stopped", "lost"}:
            for field in ("report_alive", "automation_alive", "email_alive"):
                if field in status:
                    status[field] = False
        return {**status, "state": state, "owner": row["owner"], "heartbeat_age_seconds": round(age, 1),
                "report_alive": bool(status.get("report_alive")) and state in {"running", "stalled"}}

    def enqueue(self, kind: str, device_id: str, payload: dict, *, key: str | None = None, capacity: int = 256) -> bool:
        now = time()
        with self.connect() as connection:
            self.begin_immediate(connection)
            count = connection.execute("SELECT COUNT(*) FROM background_tasks WHERE status='pending'").fetchone()[0]
            status = "pending" if count < capacity else "deferred"
            added = connection.execute("""
                INSERT OR IGNORE INTO background_tasks
                    (task_id, kind, device_id, payload, dedup_key, status, created_at, updated_at, next_attempt, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (uuid4().hex, kind, device_id, json.dumps(payload), key or uuid4().hex, status, now, now, now,
                  "QUEUE_CAPACITY" if status == "deferred" else None)).rowcount == 1
        if added and status == "deferred":
            logger.warning("background queue full; task persisted for later admission: kind=%s device=%s", kind, device_id)
        return added

    def admit_deferred(self, *, capacity: int = 256) -> None:
        with self.connect() as connection:
            self.begin_immediate(connection)
            count = connection.execute("SELECT COUNT(*) FROM background_tasks WHERE status='pending'").fetchone()[0]
            connection.execute("""UPDATE background_tasks SET status='pending', updated_at=?
                WHERE task_id IN (SELECT task_id FROM background_tasks WHERE status='deferred'
                    AND next_attempt<=? ORDER BY created_at LIMIT ?)""", (time(), time(), max(0, capacity - count)))

    def claim_task(self, owner: str, *, analysis: bool) -> dict | None:
        with self.connect() as connection:
            self.begin_immediate(connection)
            self._require_owner(connection, owner)
            row = connection.execute("""SELECT * FROM background_tasks WHERE status='pending' AND next_attempt<=?
                AND (kind LIKE 'analysis_%')=?
                AND NOT EXISTS (SELECT 1 FROM background_tasks active
                    WHERE active.device_id=background_tasks.device_id AND active.kind=background_tasks.kind
                        AND active.status='running' AND background_tasks.kind NOT LIKE 'analysis_%')
                ORDER BY created_at, task_id LIMIT 1""", (time(), int(analysis))).fetchone()
            if row is None:
                return None
            connection.execute("""UPDATE background_tasks SET status='running', owner=?, updated_at=?, attempts=attempts+1
                WHERE task_id=? AND status='pending'""", (owner, time(), row["task_id"]))
            return {**dict(row), "owner": owner, "payload": json.loads(row["payload"]), "attempts": row["attempts"] + 1}

    def finish_task(self, task: dict, owner: str, error: str | None = None) -> None:
        delay = min(30 * 2 ** min(task["attempts"] - 1, 7), 3600) if error else 0
        with self.connect() as connection:
            self.begin_immediate(connection)
            self._require_owner(connection, owner)
            connection.execute("""UPDATE background_tasks SET status=?, owner=NULL, updated_at=?, next_attempt=?, last_error=?
                WHERE task_id=? AND owner=? AND status='running'""",
                ("deferred" if error else "completed", time(), time() + delay, error, task["task_id"], owner))

    def recover_tasks(self, owner: str) -> None:
        with self.connect() as connection:
            self.begin_immediate(connection)
            self._require_owner(connection, owner)
            connection.execute("""UPDATE background_tasks SET status='deferred', owner=NULL,
                updated_at=?, next_attempt=?, last_error='INTERRUPTED'
                WHERE status='running' AND owner IS NOT ?""", (time(), time(), owner))

    def task_counts(self) -> dict:
        with self.connect() as connection:
            return {row[0]: row[1] for row in connection.execute("SELECT status, COUNT(*) FROM background_tasks GROUP BY status")}

    def cleanup_completed(self, *, days: int = 30) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM background_tasks WHERE status='completed' AND updated_at<?", (time() - days * 86400,))
            connection.execute("DELETE FROM llm_waiters WHERE expires_at<?", (time(),))
            connection.execute("DELETE FROM analysis_generations WHERE status!='running' AND updated_at<?", (time() - days * 86400,))

    def active(self, kind: str, device_id: str, payload: dict) -> bool:
        with self.connect() as connection:
            return connection.execute("""SELECT 1 FROM background_tasks WHERE kind=? AND device_id=? AND payload=?
                AND status IN ('pending','running','deferred') LIMIT 1""", (kind, device_id, json.dumps(payload))).fetchone() is not None

    def due(self, key: str, interval: float) -> float | None:
        now = time()
        with self.connect() as connection:
            self.begin_immediate(connection)
            connection.execute("INSERT OR IGNORE INTO background_schedules VALUES (?, ?)", (key, now))
            stamp = connection.execute("SELECT next_due FROM background_schedules WHERE schedule_key=?", (key,)).fetchone()[0]
            if stamp > now:
                return None
            connection.execute("UPDATE background_schedules SET next_due=? WHERE schedule_key=?", (now + interval, key))
            return stamp

    def schedule(self, kind: str, device_id: str, payload: dict, interval: float) -> bool:
        with self.connect() as connection:
            self.begin_immediate(connection)
            bound = CoordinationRepository(self.database, connection=connection)
            stamp = bound.due(f"{kind}:{device_id}", interval)
            if stamp is None:
                return False
            bound.enqueue(kind, device_id, payload, key=f"{kind}:{device_id}:{stamp}")
            return True

    def refresh_active(self, device_id: str, month: str) -> bool:
        with self.connect() as connection:
            rows = connection.execute("""SELECT payload FROM background_tasks WHERE kind='leadership_refresh'
                AND device_id=? AND status IN ('pending','running','deferred')""", (device_id,)).fetchall()
            return any(json.loads(row[0])["date"] == month for row in rows)

    def request_refresh(self, device_id: str, month: str, *, bootstrap: bool = False) -> bool:
        with self.connect() as connection:
            self.begin_immediate(connection)
            bound = CoordinationRepository(self.database, connection=connection)
            if bound.refresh_active(device_id, month):
                if bootstrap:
                    rows = connection.execute("""SELECT task_id, payload FROM background_tasks WHERE kind='leadership_refresh'
                        AND device_id=? AND status IN ('pending','deferred')""", (device_id,)).fetchall()
                    for row in rows:
                        payload = json.loads(row["payload"])
                        if payload["date"] == month:
                            payload["bootstrap"] = True
                            connection.execute("UPDATE background_tasks SET payload=? WHERE task_id=?", (json.dumps(payload), row["task_id"]))
                return False
            return bound.enqueue("leadership_refresh", device_id, {"date": month, "bootstrap": bootstrap})

    @staticmethod
    def _snapshot_task_key(line_id: str, payload: dict) -> str:
        from app.database.report_snapshots import fingerprint
        identity = {key: payload[key] for key in ('report_type', 'query', 'source_fingerprint')}
        return f'report_snapshot:{line_id}:{fingerprint(identity)}'

    def snapshot_refresh_active(self, line_id: str, payload: dict) -> bool:
        with self.connect() as connection:
            return connection.execute('''SELECT 1 FROM background_tasks WHERE dedup_key=?
                AND status IN ('pending', 'running', 'deferred')''',
                (self._snapshot_task_key(line_id, payload),)).fetchone() is not None

    def request_snapshot_refresh(self, line_id: str, payload: dict, *, interval: float | None = None) -> bool:
        key = self._snapshot_task_key(line_id, payload)
        now = time()
        with self.connect() as connection:
            self.begin_immediate(connection)
            existing = connection.execute('SELECT status, updated_at FROM background_tasks WHERE dedup_key=?', (key,)).fetchone()
            if existing and (existing['status'] != 'completed' or interval is not None and existing['updated_at'] + interval > now):
                return False
            active = connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='report_snapshot_refresh' AND status!='completed'").fetchone()[0]
            if active >= 64:
                return False
            count = connection.execute("SELECT COUNT(*) FROM background_tasks WHERE status='pending'").fetchone()[0]
            status = 'pending' if count < 256 else 'deferred'
            if existing:
                connection.execute('''UPDATE background_tasks SET status=?, owner=NULL, attempts=0,
                    created_at=?, updated_at=?, next_attempt=?, last_error=NULL WHERE dedup_key=?''', (status, now, now, now, key))
                return True
            return connection.execute('''INSERT INTO background_tasks
                (task_id, kind, device_id, payload, dedup_key, status, created_at, updated_at, next_attempt)
                VALUES (?, 'report_snapshot_refresh', ?, ?, ?, ?, ?, ?, ?)''',
                (uuid4().hex, line_id, json.dumps(payload), key, status, now, now, now)).rowcount == 1
