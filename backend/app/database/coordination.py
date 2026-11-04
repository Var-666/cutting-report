from __future__ import annotations
from typing import Any

import json
import logging
from contextlib import contextmanager
from psycopg.errors import UniqueViolation
from time import time
from uuid import uuid4

from app.database.postgres import PostgresRepository, ExecutionLeaseLost, lock_key, execution_scope


logger = logging.getLogger(__name__)


class CoordinationRepository(PostgresRepository):
    @contextmanager
    def _owned_connection(self, owner):
        with execution_scope(self.database, 'worker', owner), self.connect() as connection:
            yield connection

    def acquire(self, name: str, *, ttl: float) -> str | None:
        owner = uuid4().hex
        with self.connect() as connection:
            updated = connection.execute("""
                INSERT INTO process_leases(name, owner, expires_at, heartbeat_at)
                VALUES (%s, %s, EXTRACT(EPOCH FROM clock_timestamp())+%s, EXTRACT(EPOCH FROM clock_timestamp()))
                ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,
                    expires_at=EXTRACT(EPOCH FROM clock_timestamp())+%s,
                    heartbeat_at=EXTRACT(EPOCH FROM clock_timestamp()), status_json='{}'
                WHERE process_leases.expires_at<=EXTRACT(EPOCH FROM clock_timestamp())
                RETURNING owner
            """, (name, owner, ttl, ttl)).fetchone()
            return updated['owner'] if updated else None

    def renew(self, name: str, owner: str, *, ttl: float, status: dict | None = None) -> bool:
        with self.connect() as connection:
            # UPDATE predicates may be evaluated before waiting on an unchanged
            # locked row. Take that lock first, then check the live database clock.
            connection.execute('SELECT name FROM process_leases WHERE name=%s FOR UPDATE', (name,)).fetchone()
            updated = connection.execute("""
                UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())+%s,
                    heartbeat_at=EXTRACT(EPOCH FROM clock_timestamp()), status_json=COALESCE(%s, status_json)
                WHERE name=%s AND owner=%s AND expires_at>EXTRACT(EPOCH FROM clock_timestamp())
            """, (ttl, json.dumps(status) if status is not None else None, name, owner))
            return updated.rowcount == 1

    def release(self, name: str, owner: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE process_leases SET expires_at=0 WHERE name=%s AND owner=%s", (name, owner))

    def worker_status(self) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT *, EXTRACT(EPOCH FROM clock_timestamp())::double precision AS observed_at FROM process_leases WHERE name='worker'").fetchone()
        if row is None:
            return {"state": "missing", "report_alive": False, "stalled_workers": []}
        status = json.loads(row["status_json"])
        age = max(0, row["observed_at"] - row["heartbeat_at"])
        state = "stopped" if row["expires_at"] == 0 else "lost" if row["expires_at"] <= row["observed_at"] else "running"
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
            lock_key(connection, 'background:admission')
            count = connection.execute("SELECT COUNT(*) AS count FROM background_tasks WHERE status='pending'").fetchone()['count']
            status = "pending" if count < capacity else "deferred"
            added = connection.execute("""
                INSERT INTO background_tasks
                    (task_id, kind, device_id, payload, dedup_key, status, created_at, updated_at, next_attempt, last_error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(dedup_key) DO NOTHING
            """, (uuid4().hex, kind, device_id, json.dumps(payload), key or uuid4().hex, status, now, now, now,
                  "QUEUE_CAPACITY" if status == "deferred" else None)).rowcount == 1
        if added and status == "deferred":
            logger.warning("background queue full; task persisted for later admission: kind=%s device=%s", kind, device_id)
        return added

    def admit_deferred(self, *, capacity: int = 256) -> None:
        with self.connect() as connection:
            lock_key(connection, 'background:admission')
            if connection.execute("SELECT 1 FROM background_tasks WHERE status='deferred' AND next_attempt<=%s LIMIT 1",
                                  (time(),)).fetchone() is None:
                return
            if connection.execute("SELECT COUNT(*) AS count FROM background_tasks WHERE status='pending'").fetchone()['count'] >= capacity:
                return
            count = connection.execute("SELECT COUNT(*) AS count FROM background_tasks WHERE status='pending'").fetchone()['count']
            connection.execute("""UPDATE background_tasks SET status='pending', updated_at=%s
                WHERE task_id IN (SELECT task_id FROM background_tasks WHERE status='deferred'
                    AND next_attempt<=%s ORDER BY created_at LIMIT %s)""", (time(), time(), max(0, capacity - count)))

    def claim_task(self, owner: str, *, analysis: bool) -> dict | None:
        with self._owned_connection(owner) as connection:
            row = connection.execute("""SELECT task.* FROM background_tasks task
                WHERE task.status='pending' AND task.next_attempt<=%s
                  AND (task.kind LIKE 'analysis_%%')=%s
                  AND NOT EXISTS (SELECT 1 FROM background_tasks active
                      WHERE active.device_id=task.device_id AND active.kind=task.kind
                        AND active.status='running' AND task.kind NOT LIKE 'analysis_%%')
                ORDER BY task.created_at, task.task_id LIMIT 1 FOR UPDATE OF task SKIP LOCKED""", (time(), analysis)).fetchone()
            if row is None:
                return None
            # The partial unique index also protects same-line operation slots when
            # two transactions select different pending rows before either commits.
            try:
                with connection.transaction():
                    row = connection.execute("""UPDATE background_tasks
                        SET status='running', owner=%s, updated_at=%s, attempts=attempts+1
                        WHERE task_id=%s AND status='pending' RETURNING *""", (owner, time(), row['task_id'])).fetchone()
            except UniqueViolation as exc:
                if exc.diag.constraint_name != 'idx_background_one_operation':
                    raise
                return None
            return {**row, 'payload': json.loads(row['payload'])} if row else None

    def finish_task(self, task: dict, owner: str, error: str | None = None) -> float | None:
        delay = min(30 * 2 ** min(task["attempts"] - 1, 7), 3600) if error else 0
        with self._owned_connection(owner) as connection:
            now = time()
            updated = connection.execute("""UPDATE background_tasks SET status=%s, owner=NULL, updated_at=%s, next_attempt=%s, last_error=%s
                WHERE task_id=%s AND owner=%s AND status='running'""",
                ("deferred" if error else "completed", now, now + delay, error, task["task_id"], owner))
        return now if updated.rowcount else None

    def recover_tasks(self, owner: str) -> None:
        with self._owned_connection(owner) as connection:
            connection.execute("""UPDATE background_tasks SET status='deferred', owner=NULL,
                updated_at=%s, next_attempt=%s, last_error='INTERRUPTED'
                WHERE status='running' AND owner IS DISTINCT FROM %s""", (time(), time(), owner))

    def task_counts(self) -> dict:
        with self.connect() as connection:
            return {row['status']: row['count'] for row in connection.execute("SELECT status, COUNT(*) AS count FROM background_tasks GROUP BY status")}

    def cleanup_completed(self, *, days: int = 30) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM background_tasks WHERE status='completed' AND updated_at<%s", (time() - days * 86400,))
            connection.execute("DELETE FROM llm_waiters WHERE expires_at<%s", (time(),))
            connection.execute("DELETE FROM analysis_generations WHERE status!='running' AND updated_at<%s", (time() - days * 86400,))

    def active(self, kind: str, device_id: str, payload: dict) -> bool:
        with self.connect() as connection:
            return connection.execute("""SELECT 1 FROM background_tasks WHERE kind=%s AND device_id=%s AND payload=%s
                AND status IN ('pending','running','deferred') LIMIT 1""", (kind, device_id, json.dumps(payload))).fetchone() is not None

    def task_exists(self, key: str) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM background_tasks WHERE dedup_key=%s", (key,)).fetchone() is not None

    @staticmethod
    def _schedule_due(row: dict[str, Any], interval: float) -> float:
        if row['interval'] is None or row['interval'] == interval:
            return row['next_due']
        return row['next_due'] - row['interval'] + interval

    def next_due(self, key: str, interval: float) -> float:
        with self.connect() as connection:
            row = connection.execute("SELECT next_due, interval FROM background_schedules WHERE schedule_key=%s", (key,)).fetchone()
            if row and row['interval'] is None:
                # Adopt pre-P2 cadence once while preserving the persisted deadline.
                connection.execute("UPDATE background_schedules SET interval=%s WHERE schedule_key=%s AND interval IS NULL", (interval, key))
                row = connection.execute("SELECT next_due, interval FROM background_schedules WHERE schedule_key=%s", (key,)).fetchone()
            return self._schedule_due(row, interval) if row else time()

    def due(self, key: str, interval: float) -> float | None:
        now = time()
        with self.connect() as connection:
            connection.execute("INSERT INTO background_schedules(schedule_key, next_due, interval) VALUES (%s, %s, %s) ON CONFLICT(schedule_key) DO NOTHING", (key, now, interval))
            row = connection.execute("SELECT next_due, interval FROM background_schedules WHERE schedule_key=%s FOR UPDATE", (key,)).fetchone()
            stamp = self._schedule_due(row, interval)
            if stamp > now:
                if row['interval'] != interval:
                    connection.execute("UPDATE background_schedules SET next_due=%s, interval=%s WHERE schedule_key=%s", (stamp, interval, key))
                return None
            connection.execute("UPDATE background_schedules SET next_due=%s, interval=%s WHERE schedule_key=%s", (now + interval, interval, key))
            return stamp

    def schedule(self, kind: str, device_id: str, payload: dict, interval: float) -> float:
        with self.connect() as connection:
            bound = CoordinationRepository(self.database, connection=connection)
            stamp = bound.due(f"{kind}:{device_id}", interval)
            if stamp is not None:
                key = f"{kind}:{device_id}:{stamp}"
                if not bound.enqueue(kind, device_id, payload, key=key) and not bound.task_exists(key):
                    raise RuntimeError("周期任务未持久化，拒绝推进调度进度")
            return bound.next_due(f"{kind}:{device_id}", interval)

    def refresh_active(self, device_id: str, month: str, *, source_fingerprint: str | None = None) -> bool:
        with self.connect() as connection:
            rows = connection.execute("""SELECT payload FROM background_tasks WHERE kind='leadership_refresh'
                AND device_id=%s AND status IN ('pending','running','deferred')""", (device_id,)).fetchall()
            return any(json.loads(row['payload'])["date"] == month and (source_fingerprint is None or json.loads(row['payload']).get("source_fingerprint") == source_fingerprint) for row in rows)

    def request_refresh(self, device_id: str, month: str, *, bootstrap: bool = False, source_fingerprint: str | None = None) -> bool:
        with self.connect() as connection:
            lock_key(connection, 'leadership-refresh:' + device_id + ':' + month)
            bound = CoordinationRepository(self.database, connection=connection)
            if bound.refresh_active(device_id, month, source_fingerprint=source_fingerprint):
                if bootstrap:
                    rows = connection.execute("""SELECT task_id, payload FROM background_tasks WHERE kind='leadership_refresh'
                        AND device_id=%s AND status IN ('pending','deferred')""", (device_id,)).fetchall()
                    for row in rows:
                        payload = json.loads(row["payload"])
                        if payload["date"] == month and (source_fingerprint is None or payload.get("source_fingerprint") == source_fingerprint):
                            payload["bootstrap"] = True
                            connection.execute("UPDATE background_tasks SET payload=%s WHERE task_id=%s", (json.dumps(payload), row["task_id"]))
                return False
            return bound.enqueue("leadership_refresh", device_id, {"date": month, "bootstrap": bootstrap, "source_fingerprint": source_fingerprint})

    @staticmethod
    def snapshot_task_key(line_id: str, payload: dict) -> str:
        from app.database.report_snapshots import fingerprint
        identity = {key: payload[key] for key in ('report_type', 'query', 'source_fingerprint')}
        return f'report_snapshot:{line_id}:{fingerprint(identity)}'

    def snapshot_refresh_active(self, line_id: str, payload: dict) -> bool:
        with self.connect() as connection:
            return connection.execute('''SELECT 1 FROM background_tasks WHERE dedup_key=%s
                AND status IN ('pending', 'running', 'deferred')''',
                (self.snapshot_task_key(line_id, payload),)).fetchone() is not None

    def snapshot_refresh_due(self, line_id: str, payload: dict, interval: float) -> float:
        with self.connect() as connection:
            row = connection.execute('SELECT status, updated_at FROM background_tasks WHERE dedup_key=%s',
                                     (self.snapshot_task_key(line_id, payload),)).fetchone()
            if row is None:
                return time()
            # An active task cannot be refreshed until it completes, then waits its full interval.
            return row['updated_at'] + interval if row['status'] == 'completed' else time() + interval

    def request_snapshot_refresh(self, line_id: str, payload: dict, *, interval: float | None = None) -> bool:
        key = self.snapshot_task_key(line_id, payload)
        now = time()
        with self.connect() as connection:
            lock_key(connection, 'background:admission')
            existing = connection.execute('SELECT status, updated_at FROM background_tasks WHERE dedup_key=%s', (key,)).fetchone()
            if existing and (existing['status'] != 'completed' or interval is not None and existing['updated_at'] + interval > now):
                return False
            active = connection.execute("SELECT COUNT(*) AS count FROM background_tasks WHERE kind='report_snapshot_refresh' AND status!='completed'").fetchone()['count']
            if active >= 64:
                return False
            count = connection.execute("SELECT COUNT(*) AS count FROM background_tasks WHERE status='pending'").fetchone()['count']
            status = 'pending' if count < 256 else 'deferred'
            if existing:
                connection.execute('''UPDATE background_tasks SET status=%s, owner=NULL, attempts=0,
                    created_at=%s, updated_at=%s, next_attempt=%s, last_error=NULL WHERE dedup_key=%s''', (status, now, now, now, key))
                return True
            return connection.execute('''INSERT INTO background_tasks
                (task_id, kind, device_id, payload, dedup_key, status, created_at, updated_at, next_attempt)
                VALUES (%s, 'report_snapshot_refresh', %s, %s, %s, %s, %s, %s, %s)''',
                (uuid4().hex, line_id, json.dumps(payload), key, status, now, now, now)).rowcount == 1
