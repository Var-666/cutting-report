from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from app.database.sqlite import SQLiteDatabase, SQLiteRepository

from uuid import uuid4
from app.database.workshop_scope import WorkshopScopeRepository
from app.database.users import UserRepository
from app.database.workshop_errors import ConfirmationConflictError, ConfirmationNotFoundError, ConfirmationPermissionError

class ConfirmationRepository(WorkshopScopeRepository):
    def get_confirmations(
        self, task_ids: list[str], *, report_id: str | None = None
    ) -> dict[str, dict[str, Any]]:
        self.require_scope()
        unique_ids = list(dict.fromkeys(str(task_id) for task_id in task_ids))
        if not unique_ids:
            return {}
        result: dict[str, dict[str, Any]] = {}
        with self.connect() as connection:
            for start in range(0, len(unique_ids), 500):
                chunk = unique_ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                report_clause = ""
                parameters: list[Any] = list(chunk)
                if report_id is not None:
                    report_clause = " AND (c.report_id = ? OR c.report_id IS NULL)"
                    parameters.append(report_id)
                if self.scope_machine_no is not None:
                    report_clause += " AND c.scope_machine_no = ? COLLATE NOCASE"
                    parameters.append(self.scope_machine_no)
                rows = connection.execute(
                    f"""
                    SELECT c.*, u.company_no AS worker_company_no
                    FROM workshop_confirmations AS c
                    LEFT JOIN users AS u ON u.user_id = c.worker_id
                    WHERE c.task_id IN ({placeholders}){report_clause}
                    """,
                    parameters,
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
        snapshot_revision: int | None = None,
    ) -> dict[str, Any]:
        return self.confirm_tasks(
            task_ids=[task_id],
            user=user,
            now=now,
            report_id=report_id,
            snapshot_revisions={task_id: snapshot_revision} if snapshot_revision is not None else None,
        )[0]

    def confirm_tasks(
        self,
        *,
        task_ids: list[str],
        user: dict[str, Any],
        now: str,
        report_id: str | None = None,
        snapshot_revisions: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        self.require_scope()
        unique_ids = list(dict.fromkeys(str(task_id) for task_id in task_ids))
        if not unique_ids:
            raise ValueError("至少选择一个待确认任务")
        placeholders = ",".join("?" for _ in unique_ids)

        with self.connect() as connection:
            self.begin_immediate(connection)
            self._check_report_scope(connection, report_id)
            if report_id:
                report_revisions = {
                    str(row["task_id"]): int(row["snapshot_revision"])
                    for row in connection.execute(
                        f"""
                        SELECT task_id, snapshot_revision FROM workshop_report_items
                        WHERE report_id = ? AND task_id IN ({placeholders})
                        """,
                        (report_id, *unique_ids),
                    ).fetchall()
                }
                if any(task_id not in report_revisions for task_id in unique_ids):
                    raise ValueError("部分任务不在当日车间报表中")
            else:
                report_revisions = {
                    str(row["task_id"]): int(row["snapshot_revision"])
                    for row in connection.execute(
                        f"SELECT i.task_id, MAX(i.snapshot_revision) AS snapshot_revision FROM workshop_report_items i JOIN workshop_reports r ON r.report_id=i.report_id WHERE i.task_id IN ({placeholders}) AND r.scope_machine_no=? COLLATE NOCASE GROUP BY i.task_id",
                        (*unique_ids, self.require_scope()),
                    ).fetchall()
                }
            for task_id, revision in report_revisions.items():
                expected = (snapshot_revisions or {}).get(task_id)
                if (revision > 1 and expected is None) or (expected is not None and expected != revision):
                    raise ConfirmationConflictError("日报任务版本已变化，请刷新后重新确认")

            existing_rows = connection.execute(
                f"""
                SELECT * FROM workshop_confirmations
                WHERE task_id IN ({placeholders}) AND scope_machine_no=? COLLATE NOCASE
                """,
                (*unique_ids, self.require_scope()),
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
                        confirmed_at, created_at, updated_at, scope_machine_no
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        report_id,
                        user["user_id"],
                        user["display_name"],
                        now,
                        now,
                        now,
                        self.require_scope(),
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
                WHERE task_id IN ({placeholders}) AND scope_machine_no=? COLLATE NOCASE
                """,
                (*unique_ids, self.require_scope()),
            ).fetchall()
            saved_by_id = {str(row["task_id"]): dict(row) for row in saved_rows}
        return [saved_by_id[task_id] for task_id in unique_ids]

    def _insert_confirmation_audit(
        self, connection: sqlite3.Connection,
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
                operated_by_id, operated_by_name, operated_by_role, operated_at, scope_machine_no
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                previous["scope_machine_no"] if previous else self.require_scope(),
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
            self._check_report_scope(connection, report_id)
            existing = connection.execute(
                "SELECT * FROM workshop_confirmations WHERE task_id = ? AND scope_machine_no = ? COLLATE NOCASE", (task_id, self.require_scope())
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
                "DELETE FROM workshop_confirmations WHERE task_id = ? AND scope_machine_no=? COLLATE NOCASE", (task_id, self.require_scope())
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
            self._check_report_scope(connection, report_id)
            existing = connection.execute(
                "SELECT * FROM workshop_confirmations WHERE task_id = ? AND scope_machine_no = ? COLLATE NOCASE", (task_id, self.require_scope())
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
                new_worker = UserRepository.public_user(target)
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
                    WHERE task_id = ? AND scope_machine_no=? COLLATE NOCASE
                    """,
                    (
                        existing["report_id"] or report_id,
                        new_worker["user_id"],
                        new_worker["display_name"],
                        now,
                        now,
                        task_id, self.require_scope(),
                    ),
                )
            else:
                connection.execute(
                    "DELETE FROM workshop_confirmations WHERE task_id = ? AND scope_machine_no=? COLLATE NOCASE", (task_id, self.require_scope())
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
        self.require_scope()
        clauses: list[str] = []
        params: list[Any] = []
        if self.scope_machine_no is not None:
            clauses.append("scope_machine_no = ? COLLATE NOCASE")
            params.append(self.scope_machine_no)
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
