from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.models.domain import WorkshopRecord


PASSWORD_ITERATIONS = 310_000


class AuthenticationError(ValueError):
    pass


class ConfirmationConflictError(ValueError):
    pass


class DailyReportExistsError(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


class WorkshopRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'worker')),
                    auth_source TEXT NOT NULL DEFAULT 'local',
                    external_id TEXT,
                    group_no TEXT,
                    company_no TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_users_active_name
                    ON users(active, display_name);

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires
                    ON auth_sessions(expires_at);

                CREATE TABLE IF NOT EXISTS workshop_reports (
                    report_id TEXT PRIMARY KEY,
                    report_date TEXT NOT NULL UNIQUE,
                    source_mode TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    generated_by_id TEXT NOT NULL,
                    generated_by_name TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(generated_by_id) REFERENCES users(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_workshop_reports_generated
                    ON workshop_reports(generated_at DESC);

                CREATE TABLE IF NOT EXISTS workshop_report_items (
                    report_item_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    project_name TEXT,
                    segment_name TEXT,
                    program_no TEXT,
                    cut_length TEXT,
                    groove_length TEXT,
                    work_time TEXT,
                    production_time TEXT,
                    remark TEXT,
                    UNIQUE(report_id, task_id),
                    FOREIGN KEY(report_id) REFERENCES workshop_reports(report_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_workshop_items_report_order
                    ON workshop_report_items(report_id, ordinal);

                CREATE TABLE IF NOT EXISTS workshop_confirmations (
                    task_id TEXT PRIMARY KEY,
                    report_id TEXT,
                    worker_id TEXT NOT NULL,
                    worker_name TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(worker_id) REFERENCES users(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_workshop_confirmations_worker
                    ON workshop_confirmations(worker_id, confirmed_at DESC);
                """
            )
            user_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(users)").fetchall()
            }
            for column, definition in (
                ("auth_source", "TEXT NOT NULL DEFAULT 'local'"),
                ("external_id", "TEXT"),
                ("group_no", "TEXT"),
                ("company_no", "TEXT"),
            ):
                if column not in user_columns:
                    connection.execute(
                        f"ALTER TABLE users ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_identity
                ON users(auth_source, external_id)
                WHERE external_id IS NOT NULL
                """
            )
            confirmation_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(workshop_confirmations)"
                ).fetchall()
            }
            if "report_id" not in confirmation_columns:
                connection.execute(
                    "ALTER TABLE workshop_confirmations ADD COLUMN report_id TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workshop_confirmations_report
                ON workshop_confirmations(report_id, confirmed_at DESC)
                """
            )

    @staticmethod
    def _public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        keys = set(row.keys())
        auth_source = row["auth_source"] if "auth_source" in keys else "local"
        company_no = row["company_no"] if "company_no" in keys else None
        return {
            "user_id": row["user_id"],
            "username": company_no if auth_source == "hr_database" else row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "auth_source": auth_source,
            "external_id": row["external_id"] if "external_id" in keys else None,
            "group_no": row["group_no"] if "group_no" in keys else None,
            "company_no": company_no,
            "active": bool(row["active"]),
            "created_at": row["created_at"],
        }

    def user_count(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def admin_count(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin'"
                ).fetchone()[0]
            )

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        role: str,
        now: str,
    ) -> dict[str, Any]:
        user_id = f"usr_{uuid4().hex[:16]}"
        try:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, username, display_name, password_hash, role,
                        active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id,
                        username.strip(),
                        display_name.strip(),
                        hash_password(password),
                        role,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("登录账号已存在") from exc
        return self._public_user(row)

    def bootstrap_admin(
        self, *, username: str, display_name: str, password: str, now: str
    ) -> dict[str, Any]:
        user_id = f"usr_{uuid4().hex[:16]}"
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin'"
            ).fetchone()[0]:
                raise AuthenticationError("系统已经完成账号初始化")
            connection.execute(
                """
                INSERT INTO users (
                    user_id, username, display_name, password_hash, role,
                    active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'admin', 1, ?, ?)
                """,
                (
                    user_id,
                    username.strip(),
                    display_name.strip(),
                    hash_password(password),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._public_user(row)

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE username = ? COLLATE NOCASE
                  AND active = 1
                  AND auth_source = 'local'
                """,
                (username.strip(),),
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise AuthenticationError("账号或密码错误")
        return self._public_user(row)

    def upsert_external_worker(
        self,
        *,
        external_id: str,
        company_no: str,
        group_no: str,
        display_name: str,
        now: str,
    ) -> dict[str, Any]:
        """Create/update the local identity used by sessions and confirmations.

        No HR password is stored. The internal username is based on the stable HR
        identifier so a company-number change does not break history.
        """
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE auth_source = 'hr_database' AND external_id = ?
                """,
                (external_id,),
            ).fetchone()
            if row:
                connection.execute(
                    """
                    UPDATE users
                    SET display_name = ?, group_no = ?, company_no = ?,
                        active = 1, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (display_name, group_no, company_no, now, row["user_id"]),
                )
                user_id = row["user_id"]
            else:
                user_id = f"usr_{uuid4().hex[:16]}"
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, username, display_name, password_hash, role,
                        auth_source, external_id, group_no, company_no,
                        active, created_at, updated_at
                    ) VALUES (?, ?, ?, '', 'worker', 'hr_database', ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id,
                        f"hr:{external_id}",
                        display_name,
                        external_id,
                        group_no,
                        company_no,
                        now,
                        now,
                    ),
                )
            saved = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._public_user(saved)

    @staticmethod
    def _session_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(
        self, user_id: str, *, now: datetime, hours: int
    ) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(hours=hours)
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?", (now.isoformat(),)
            )
            connection.execute(
                """
                INSERT INTO auth_sessions(session_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self._session_hash(token),
                    user_id,
                    now.isoformat(timespec="seconds"),
                    expires_at.isoformat(timespec="seconds"),
                ),
            )
        return token, expires_at.isoformat(timespec="seconds")

    def user_for_session(self, token: str | None, *, now: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT u.*
                FROM auth_sessions AS s
                JOIN users AS u ON u.user_id = s.user_id
                WHERE s.session_hash = ? AND s.expires_at > ? AND u.active = 1
                """,
                (self._session_hash(token), now),
            ).fetchone()
        return self._public_user(row) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE session_hash = ?",
                (self._session_hash(token),),
            )

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY active DESC, display_name, username"
            ).fetchall()
        return [self._public_user(row) for row in rows]

    @staticmethod
    def _report_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "report_id": row["report_id"],
            "report_date": row["report_date"],
            "source_mode": row["source_mode"],
            "item_count": int(row["item_count"]),
            "confirmed_count": int(row["confirmed_count"]),
            "generated_by_id": row["generated_by_id"],
            "generated_by_name": row["generated_by_name"],
            "generated_at": row["generated_at"],
        }

    def get_daily_report(self, report_date: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT r.*,
                       COUNT(c.task_id) AS confirmed_count
                FROM workshop_reports AS r
                LEFT JOIN workshop_report_items AS i ON i.report_id = r.report_id
                LEFT JOIN workshop_confirmations AS c ON c.task_id = i.task_id
                WHERE r.report_date = ?
                GROUP BY r.report_id
                """,
                (report_date,),
            ).fetchone()
        return self._report_dict(row)

    def create_daily_report(
        self,
        *,
        report_date: str,
        source_mode: str,
        records: list[WorkshopRecord],
        user: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        report_id = f"wdr_{uuid4().hex[:16]}"
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT report_id FROM workshop_reports WHERE report_date = ?",
                (report_date,),
            ).fetchone()
            if existing:
                raise DailyReportExistsError("当日车间报表已经生成")

            unique_records: list[WorkshopRecord] = []
            seen_task_ids: set[str] = set()
            for record in records:
                task_id = str(record.task_id)
                if task_id in seen_task_ids:
                    continue
                seen_task_ids.add(task_id)
                unique_records.append(record)

            connection.execute(
                """
                INSERT INTO workshop_reports (
                    report_id, report_date, source_mode, item_count,
                    generated_by_id, generated_by_name, generated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    report_date,
                    source_mode,
                    len(unique_records),
                    user["user_id"],
                    user["display_name"],
                    now,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO workshop_report_items (
                    report_item_id, report_id, task_id, ordinal, project_name,
                    segment_name, program_no, cut_length, groove_length,
                    work_time, production_time, remark
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"wdi_{uuid4().hex[:20]}",
                        report_id,
                        str(record.task_id),
                        ordinal,
                        record.project_code or record.ship_no,
                        record.segment_name,
                        record.program_no,
                        str(record.cut_length) if record.cut_length is not None else None,
                        str(record.groove_length) if record.groove_length is not None else None,
                        str(record.work_time) if record.work_time is not None else None,
                        record.production_time.isoformat() if record.production_time else None,
                        record.remark,
                    )
                    for ordinal, record in enumerate(unique_records, start=1)
                ],
            )
        report = self.get_daily_report(report_date)
        if report is None:
            raise RuntimeError("车间报表生成后未能读取")
        return report

    def populate_empty_daily_report(
        self,
        *,
        report_date: str,
        source_mode: str,
        records: list[WorkshopRecord],
        user: dict[str, Any],
        now: str,
    ) -> dict[str, Any] | None:
        if not records:
            return self.get_daily_report(report_date)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            report = connection.execute(
                "SELECT * FROM workshop_reports WHERE report_date = ?",
                (report_date,),
            ).fetchone()
            if report is None:
                return None
            if int(report["item_count"]) > 0:
                populated = connection.execute(
                    """
                    SELECT r.*, COUNT(c.task_id) AS confirmed_count
                    FROM workshop_reports AS r
                    LEFT JOIN workshop_report_items AS i ON i.report_id = r.report_id
                    LEFT JOIN workshop_confirmations AS c ON c.task_id = i.task_id
                    WHERE r.report_id = ?
                    GROUP BY r.report_id
                    """,
                    (report["report_id"],),
                ).fetchone()
                return self._report_dict(populated)

            unique_records: list[WorkshopRecord] = []
            seen_task_ids: set[str] = set()
            for record in records:
                task_id = str(record.task_id)
                if task_id not in seen_task_ids:
                    seen_task_ids.add(task_id)
                    unique_records.append(record)
            connection.executemany(
                """
                INSERT INTO workshop_report_items (
                    report_item_id, report_id, task_id, ordinal, project_name,
                    segment_name, program_no, cut_length, groove_length,
                    work_time, production_time, remark
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"wdi_{uuid4().hex[:20]}",
                        report["report_id"],
                        str(record.task_id),
                        ordinal,
                        record.project_code or record.ship_no,
                        record.segment_name,
                        record.program_no,
                        str(record.cut_length) if record.cut_length is not None else None,
                        str(record.groove_length) if record.groove_length is not None else None,
                        str(record.work_time) if record.work_time is not None else None,
                        record.production_time.isoformat() if record.production_time else None,
                        record.remark,
                    )
                    for ordinal, record in enumerate(unique_records, start=1)
                ],
            )
            connection.execute(
                """
                UPDATE workshop_reports
                SET source_mode = ?, item_count = ?, generated_by_id = ?,
                    generated_by_name = ?, generated_at = ?
                WHERE report_id = ?
                """,
                (
                    source_mode,
                    len(unique_records),
                    user["user_id"],
                    user["display_name"],
                    now,
                    report["report_id"],
                ),
            )
        return self.get_daily_report(report_date)

    @staticmethod
    def _optional_decimal(value: Any) -> Decimal | None:
        return Decimal(str(value)) if value not in (None, "") else None

    def get_daily_report_records(
        self, report_date: str
    ) -> tuple[dict[str, Any] | None, list[WorkshopRecord]]:
        report = self.get_daily_report(report_date)
        if report is None:
            return None, []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workshop_report_items
                WHERE report_id = ?
                ORDER BY ordinal
                """,
                (report["report_id"],),
            ).fetchall()
        confirmations = self.get_confirmations([str(row["task_id"]) for row in rows])
        records: list[WorkshopRecord] = []
        for row in rows:
            confirmation = confirmations.get(str(row["task_id"]))
            records.append(
                WorkshopRecord(
                    task_id=str(row["task_id"]),
                    project_code=row["project_name"],
                    ship_no=row["project_name"],
                    segment_name=row["segment_name"],
                    program_no=row["program_no"],
                    cut_length=self._optional_decimal(row["cut_length"]),
                    groove_length=self._optional_decimal(row["groove_length"]),
                    work_time=self._optional_decimal(row["work_time"]),
                    remark=row["remark"],
                    production_time=(
                        datetime.fromisoformat(row["production_time"])
                        if row["production_time"]
                        else None
                    ),
                    confirmed_by=confirmation["worker_name"] if confirmation else None,
                    confirmed_at=(
                        datetime.fromisoformat(confirmation["confirmed_at"])
                        if confirmation
                        else None
                    ),
                )
            )
        return report, records

    def get_confirmations(self, task_ids: list[str]) -> dict[str, dict[str, Any]]:
        unique_ids = list(dict.fromkeys(str(task_id) for task_id in task_ids))
        if not unique_ids:
            return {}
        result: dict[str, dict[str, Any]] = {}
        with self.connect() as connection:
            for start in range(0, len(unique_ids), 500):
                chunk = unique_ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT * FROM workshop_confirmations WHERE task_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                result.update({str(row["task_id"]): dict(row) for row in rows})
        return result

    def confirm_task(
        self,
        *,
        task_id: str,
        user: dict[str, Any],
        now: str,
        report_id: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if report_id and not connection.execute(
                """
                SELECT 1 FROM workshop_report_items
                WHERE report_id = ? AND task_id = ?
                """,
                (report_id, task_id),
            ).fetchone():
                raise ValueError("该任务不在当日车间报表中")
            existing = connection.execute(
                "SELECT * FROM workshop_confirmations WHERE task_id = ?", (task_id,)
            ).fetchone()
            if existing:
                if existing["worker_id"] != user["user_id"]:
                    raise ConfirmationConflictError(
                        f"该任务已由 {existing['worker_name']} 确认"
                    )
                return dict(existing)
            connection.execute(
                """
                INSERT INTO workshop_confirmations (
                    task_id, report_id, worker_id, worker_name,
                    confirmed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    report_id,
                    user["user_id"],
                    user["display_name"],
                    now,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM workshop_confirmations WHERE task_id = ?", (task_id,)
            ).fetchone()
        return dict(row)
