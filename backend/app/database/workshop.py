from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.models.domain import WorkshopRecord
from app.database.sqlite import SQLiteDatabase, SQLiteRepository


PASSWORD_ITERATIONS = 310_000


class AuthenticationError(ValueError):
    pass


class ConfirmationConflictError(ValueError):
    pass


class ConfirmationNotFoundError(ValueError):
    pass


class ConfirmationPermissionError(ValueError):
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


class WorkshopRepository(SQLiteRepository):
    def __init__(
        self,
        database: Path | SQLiteDatabase,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        super().__init__(database, connection=connection)

    def initialize(self) -> None:
        self.database.initialize()
        with self.connect() as connection:
            connection.executescript(
                """
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
                    scope_machine_no TEXT,
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
            report_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(workshop_reports)"
                ).fetchall()
            }
            if "scope_machine_no" not in report_columns:
                connection.execute(
                    "ALTER TABLE workshop_reports ADD COLUMN scope_machine_no TEXT"
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workshop_confirmation_audits (
                    audit_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    report_id TEXT,
                    action TEXT NOT NULL CHECK(action IN (
                        'confirm', 'worker_revoke', 'admin_revoke', 'admin_reassign'
                    )),
                    previous_worker_id TEXT,
                    previous_worker_name TEXT,
                    new_worker_id TEXT,
                    new_worker_name TEXT,
                    reason TEXT NOT NULL,
                    operated_by_id TEXT NOT NULL,
                    operated_by_name TEXT NOT NULL,
                    operated_by_role TEXT NOT NULL,
                    operated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workshop_confirmation_audits_task
                    ON workshop_confirmation_audits(task_id, operated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workshop_confirmation_audits_report
                    ON workshop_confirmation_audits(report_id, operated_at DESC);

                CREATE TABLE IF NOT EXISTS admin_role_audits (
                    audit_id TEXT PRIMARY KEY,
                    subject_user_id TEXT NOT NULL,
                    subject_external_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('grant', 'revoke')),
                    operated_by_id TEXT NOT NULL,
                    operated_by_name TEXT NOT NULL,
                    operated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_role_audits_subject
                    ON admin_role_audits(subject_user_id, operated_at DESC);
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
            self.begin_immediate(connection)
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
            self.begin_immediate(connection)
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

    def verify_local_user_password(self, user_id: str, password: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT password_hash FROM users
                WHERE user_id = ? AND auth_source = 'local' AND active = 1
                """,
                (user_id,),
            ).fetchone()
        return bool(row and verify_password(password, row["password_hash"]))

    def list_admins(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM users
                WHERE role = 'admin' AND active = 1
                ORDER BY auth_source, display_name, username
                """
            ).fetchall()
            admins = []
            for row in rows:
                admin = self._public_user(row)
                audit = connection.execute(
                    """
                    SELECT operated_by_name, operated_at
                    FROM admin_role_audits
                    WHERE subject_user_id = ?
                    ORDER BY operated_at DESC
                    LIMIT 1
                    """,
                    (row["user_id"],),
                ).fetchone()
                admin["permission_changed_by"] = (
                    audit["operated_by_name"] if audit else None
                )
                admin["permission_changed_at"] = audit["operated_at"] if audit else None
                admins.append(admin)
        return admins

    def grant_external_admin(
        self,
        *,
        identity: dict[str, str],
        operator: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        external_id = identity["external_id"].strip()
        company_no = identity["company_no"].strip()
        display_name = identity["display_name"].strip()
        if not external_id or not company_no or not display_name:
            raise ValueError("HR人员信息不完整")

        with self.connect() as connection:
            self.begin_immediate(connection)
            existing = connection.execute(
                """
                SELECT * FROM users
                WHERE auth_source = 'hr_database' AND external_id = ?
                """,
                (external_id,),
            ).fetchone()
            already_admin = bool(existing and existing["role"] == "admin")
            if existing:
                user_id = existing["user_id"]
                connection.execute(
                    """
                    UPDATE users
                    SET display_name = ?, group_no = ?, company_no = ?,
                        role = 'admin', active = 1, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        display_name,
                        identity.get("group_no", "").strip(),
                        company_no,
                        now,
                        user_id,
                    ),
                )
            else:
                user_id = f"usr_{uuid4().hex[:16]}"
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, username, display_name, password_hash, role,
                        auth_source, external_id, group_no, company_no,
                        active, created_at, updated_at
                    ) VALUES (?, ?, ?, '', 'admin', 'hr_database', ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id,
                        f"hr:{external_id}",
                        display_name,
                        external_id,
                        identity.get("group_no", "").strip(),
                        company_no,
                        now,
                        now,
                    ),
                )

            if not already_admin:
                connection.execute(
                    """
                    INSERT INTO admin_role_audits (
                        audit_id, subject_user_id, subject_external_id, action,
                        operated_by_id, operated_by_name, operated_at
                    ) VALUES (?, ?, ?, 'grant', ?, ?, ?)
                    """,
                    (
                        f"aar_{uuid4().hex[:16]}",
                        user_id,
                        external_id,
                        operator["user_id"],
                        operator["display_name"],
                        now,
                    ),
                )
            saved = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._public_user(saved)

    def revoke_external_admin(
        self,
        user_id: str,
        *,
        operator: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            self.begin_immediate(connection)
            target = connection.execute(
                "SELECT * FROM users WHERE user_id = ? AND role = 'admin' AND active = 1",
                (user_id,),
            ).fetchone()
            if target is None:
                raise ValueError("管理员不存在或权限已撤销")
            if target["auth_source"] != "hr_database":
                raise ValueError("应急管理员不能在此撤销")
            if target["user_id"] == operator["user_id"]:
                raise ValueError("不能撤销当前登录管理员自己的权限")
            if connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
            ).fetchone()[0] <= 1:
                raise ValueError("必须至少保留一个有效管理员")

            connection.execute(
                "UPDATE users SET role = 'worker', updated_at = ? WHERE user_id = ?",
                (now, user_id),
            )
            connection.execute(
                """
                INSERT INTO admin_role_audits (
                    audit_id, subject_user_id, subject_external_id, action,
                    operated_by_id, operated_by_name, operated_at
                ) VALUES (?, ?, ?, 'revoke', ?, ?, ?)
                """,
                (
                    f"aar_{uuid4().hex[:16]}",
                    user_id,
                    target["external_id"],
                    operator["user_id"],
                    operator["display_name"],
                    now,
                ),
            )
            saved = connection.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._public_user(saved)

    @staticmethod
    def _report_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "report_id": row["report_id"],
            "report_date": row["report_date"],
            "source_mode": row["source_mode"],
            "scope_machine_no": (
                row["scope_machine_no"] if "scope_machine_no" in row.keys() else None
            ),
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
        scope_machine_no: str | None = "Cutter01",
    ) -> dict[str, Any]:
        report_id = f"wdr_{uuid4().hex[:16]}"
        with self.connect() as connection:
            self.begin_immediate(connection)
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
                    report_id, report_date, source_mode, scope_machine_no, item_count,
                    generated_by_id, generated_by_name, generated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    report_date,
                    source_mode,
                    scope_machine_no,
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
        scope_machine_no: str | None = "Cutter01",
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            self.begin_immediate(connection)
            report = connection.execute(
                "SELECT * FROM workshop_reports WHERE report_date = ?",
                (report_date,),
            ).fetchone()
            if report is None:
                return None
            scope_matches = (
                scope_machine_no is None
                or report["scope_machine_no"] == scope_machine_no
            )
            if int(report["item_count"]) > 0 and scope_matches:
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

            if not scope_matches:
                connection.execute(
                    "DELETE FROM workshop_report_items WHERE report_id = ?",
                    (report["report_id"],),
                )

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
                SET source_mode = ?, scope_machine_no = ?, item_count = ?, generated_by_id = ?,
                    generated_by_name = ?, generated_at = ?
                WHERE report_id = ?
                """,
                (
                    source_mode,
                    scope_machine_no,
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
                    confirmed_by_id=confirmation["worker_id"] if confirmation else None,
                    confirmed_by=confirmation["worker_name"] if confirmation else None,
                    confirmed_by_company_no=(
                        confirmation["worker_company_no"] if confirmation else None
                    ),
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
                    f"""
                    SELECT c.*, u.company_no AS worker_company_no
                    FROM workshop_confirmations AS c
                    LEFT JOIN users AS u ON u.user_id = c.worker_id
                    WHERE c.task_id IN ({placeholders})
                    """,
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
        return self.confirm_tasks(
            task_ids=[task_id],
            user=user,
            now=now,
            report_id=report_id,
        )[0]

    def confirm_tasks(
        self,
        *,
        task_ids: list[str],
        user: dict[str, Any],
        now: str,
        report_id: str | None = None,
    ) -> list[dict[str, Any]]:
        unique_ids = list(dict.fromkeys(str(task_id) for task_id in task_ids))
        if not unique_ids:
            raise ValueError("至少选择一个待确认任务")
        placeholders = ",".join("?" for _ in unique_ids)

        with self.connect() as connection:
            self.begin_immediate(connection)
            if report_id:
                report_task_ids = {
                    str(row["task_id"])
                    for row in connection.execute(
                        f"""
                        SELECT task_id FROM workshop_report_items
                        WHERE report_id = ? AND task_id IN ({placeholders})
                        """,
                        (report_id, *unique_ids),
                    ).fetchall()
                }
                if any(task_id not in report_task_ids for task_id in unique_ids):
                    raise ValueError("部分任务不在当日车间报表中")

            existing_rows = connection.execute(
                f"""
                SELECT * FROM workshop_confirmations
                WHERE task_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
            existing_by_id = {str(row["task_id"]): row for row in existing_rows}
            for task_id in unique_ids:
                existing = existing_by_id.get(task_id)
                if existing and existing["worker_id"] != user["user_id"]:
                    raise ConfirmationConflictError(
                        f"该任务已由 {existing['worker_name']} 确认"
                    )

            for task_id in unique_ids:
                if task_id in existing_by_id:
                    continue
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
                self._insert_confirmation_audit(
                    connection,
                    task_id=task_id,
                    report_id=report_id,
                    action="confirm",
                    previous=None,
                    new_worker=user,
                    reason="工人确认本人完成任务",
                    operator=user,
                    now=now,
                )

            saved_rows = connection.execute(
                f"""
                SELECT * FROM workshop_confirmations
                WHERE task_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
            saved_by_id = {str(row["task_id"]): dict(row) for row in saved_rows}
        return [saved_by_id[task_id] for task_id in unique_ids]

    @staticmethod
    def _insert_confirmation_audit(
        connection: sqlite3.Connection,
        *,
        task_id: str,
        report_id: str | None,
        action: str,
        previous: sqlite3.Row | dict[str, Any] | None,
        new_worker: dict[str, Any] | None,
        reason: str,
        operator: dict[str, Any],
        now: str,
    ) -> str:
        audit_id = f"wca_{uuid4().hex[:16]}"
        connection.execute(
            """
            INSERT INTO workshop_confirmation_audits (
                audit_id, task_id, report_id, action,
                previous_worker_id, previous_worker_name,
                new_worker_id, new_worker_name, reason,
                operated_by_id, operated_by_name, operated_by_role, operated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                task_id,
                report_id,
                action,
                previous["worker_id"] if previous else None,
                previous["worker_name"] if previous else None,
                new_worker["user_id"] if new_worker else None,
                new_worker["display_name"] if new_worker else None,
                reason.strip(),
                operator["user_id"],
                operator["display_name"],
                operator["role"],
                now,
            ),
        )
        return audit_id

    @staticmethod
    def _audit_dict(row: sqlite3.Row) -> dict[str, Any]:
        labels = {
            "confirm": "确认任务",
            "worker_revoke": "工人撤销",
            "admin_revoke": "管理员撤销",
            "admin_reassign": "管理员改派",
        }
        result = dict(row)
        result["action_label"] = labels.get(result["action"], result["action"])
        return result

    def revoke_confirmation(
        self,
        *,
        task_id: str,
        report_id: str | None = None,
        user: dict[str, Any],
        reason: str,
        now: str,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            self.begin_immediate(connection)
            existing = connection.execute(
                "SELECT * FROM workshop_confirmations WHERE task_id = ?", (task_id,)
            ).fetchone()
            if existing is None:
                raise ConfirmationNotFoundError("该任务尚未确认或已被撤销")
            if existing["worker_id"] != user["user_id"]:
                raise ConfirmationPermissionError("只能撤销本人的确认记录")
            audit_id = self._insert_confirmation_audit(
                connection,
                task_id=task_id,
                report_id=existing["report_id"] or report_id,
                action="worker_revoke",
                previous=existing,
                new_worker=None,
                reason=reason,
                operator=user,
                now=now,
            )
            connection.execute(
                "DELETE FROM workshop_confirmations WHERE task_id = ?", (task_id,)
            )
            row = connection.execute(
                "SELECT * FROM workshop_confirmation_audits WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
        return self._audit_dict(row)

    def correct_confirmation(
        self,
        *,
        task_id: str,
        report_id: str | None = None,
        admin: dict[str, Any],
        reason: str,
        now: str,
        new_worker_id: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            self.begin_immediate(connection)
            existing = connection.execute(
                "SELECT * FROM workshop_confirmations WHERE task_id = ?", (task_id,)
            ).fetchone()
            if existing is None:
                raise ConfirmationNotFoundError("该任务尚未确认或已被撤销")

            new_worker = None
            action = "admin_revoke"
            if new_worker_id:
                target = connection.execute(
                    "SELECT * FROM users WHERE user_id = ? AND role = 'worker' AND active = 1",
                    (new_worker_id,),
                ).fetchone()
                if target is None:
                    raise ValueError("目标工人不存在或账号已停用")
                if target["user_id"] == existing["worker_id"]:
                    raise ConfirmationConflictError("目标工人与当前确认人相同")
                new_worker = self._public_user(target)
                action = "admin_reassign"

            audit_id = self._insert_confirmation_audit(
                connection,
                task_id=task_id,
                report_id=existing["report_id"] or report_id,
                action=action,
                previous=existing,
                new_worker=new_worker,
                reason=reason,
                operator=admin,
                now=now,
            )
            if new_worker:
                connection.execute(
                    """
                    UPDATE workshop_confirmations
                    SET report_id = ?, worker_id = ?, worker_name = ?,
                        confirmed_at = ?, updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        existing["report_id"] or report_id,
                        new_worker["user_id"],
                        new_worker["display_name"],
                        now,
                        now,
                        task_id,
                    ),
                )
            else:
                connection.execute(
                    "DELETE FROM workshop_confirmations WHERE task_id = ?", (task_id,)
                )
            row = connection.execute(
                "SELECT * FROM workshop_confirmation_audits WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
        return self._audit_dict(row)

    def list_confirmation_audits(
        self,
        *,
        task_id: str | None = None,
        report_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if report_id:
            clauses.append("report_id = ?")
            params.append(report_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM workshop_confirmation_audits
                {where}
                ORDER BY operated_at DESC, audit_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._audit_dict(row) for row in rows]
