from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from app.database.sqlite import SQLiteDatabase, SQLiteRepository

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from app.models.domain import WorkshopRecord
from app.database.workshop_scope import WorkshopScopeRepository
from app.database.confirmations import ConfirmationRepository
from app.database.workshop_errors import DailyReportRecordMismatchError, DailyReportScopeMismatchError, DailyReportSourceMismatchError, DailyReportExistsError

_SNAPSHOT_DECIMAL_FIELDS = frozenset(
    {"cut_length", "groove_length", "work_time"}
)

def _snapshot_field_equal(field: str, actual: Any, expected: Any) -> bool:
    if field in _SNAPSHOT_DECIMAL_FIELDS and actual is not None and expected is not None:
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except (TypeError, ValueError, ArithmeticError):
            pass
    return actual == expected

def _snapshot_values_equal(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(
        _snapshot_field_equal(field, actual.get(field), value)
        for field, value in expected.items()
    )

def _snapshot_values(record: WorkshopRecord, report_date: str) -> dict[str, Any]:
    return {
        "project_name": record.project_code or record.ship_no,
        "segment_name": record.segment_name,
        "program_no": record.program_no,
        "cut_length": str(record.cut_length) if record.cut_length is not None else None,
        "groove_length": str(record.groove_length) if record.groove_length is not None else None,
        "work_time": str(record.work_time) if record.work_time is not None else None,
        "production_time": record.production_time.isoformat() if record.production_time else None,
        "remark": record.remark,
        "production_date": record.production_date.isoformat() if record.production_date else report_date,
    }

def _unique_snapshot_records(
    records: list[WorkshopRecord], report_date: str
) -> list[WorkshopRecord]:
    unique: list[WorkshopRecord] = []
    values_by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        task_id = str(record.task_id)
        values = _snapshot_values(record, report_date)
        previous = values_by_id.get(task_id)
        if previous is not None:
            if not _snapshot_values_equal(previous, values):
                raise DailyReportRecordMismatchError(
                    f"源数据中同一任务存在冲突记录：{task_id}；请核对源数据"
                )
            continue
        values_by_id[task_id] = values
        unique.append(record)
    return unique


class WorkshopReportRepository(WorkshopScopeRepository):
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
                LEFT JOIN workshop_confirmations AS c
                    ON c.task_id = i.task_id
                    AND c.scope_machine_no = r.scope_machine_no COLLATE NOCASE
                    AND (c.report_id = r.report_id OR c.report_id IS NULL)
                WHERE r.report_date = ?
                  AND r.scope_machine_no = ? COLLATE NOCASE
                GROUP BY r.report_id
                """,
                (report_date, self.require_scope()),
            ).fetchone()
        return self._report_dict(row)

    def sync_daily_report(
        self,
        *,
        report_date: str,
        source_mode: str,
        records: list[WorkshopRecord],
        user: dict[str, Any],
        now: str,
        scope_machine_no: str | None = None,
    ) -> dict[str, Any]:
        """Create or append a snapshot in one transaction, including first-create races."""
        selected_scope = self.require_scope()
        if scope_machine_no is not None and scope_machine_no != selected_scope:
            raise DailyReportScopeMismatchError("日报范围与设备上下文不一致")
        scope_machine_no = selected_scope
        with self.connect() as connection:
            self.begin_immediate(connection)
            repository = WorkshopReportRepository(self.database, connection=connection, scope_machine_no=self.scope_machine_no)
            arguments = dict(
                report_date=report_date,
                source_mode=source_mode,
                records=records,
                user=user,
                now=now,
                scope_machine_no=scope_machine_no,
            )
            report = repository.populate_empty_daily_report(**arguments)
            if report is not None:
                return report
            return repository.create_daily_report(**arguments)

    def create_daily_report(
        self,
        *,
        report_date: str,
        source_mode: str,
        records: list[WorkshopRecord],
        user: dict[str, Any],
        now: str,
        scope_machine_no: str | None = None,
    ) -> dict[str, Any]:
        report_id = f"wdr_{uuid4().hex[:16]}"
        selected_scope = self.require_scope()
        if scope_machine_no is not None and scope_machine_no != selected_scope:
            raise DailyReportScopeMismatchError("日报范围与设备上下文不一致")
        scope_machine_no = selected_scope
        with self.connect() as connection:
            self.begin_immediate(connection)
            existing = connection.execute(
                "SELECT report_id FROM workshop_reports WHERE report_date = ? AND scope_machine_no = ? COLLATE NOCASE",
                (report_date, self.require_scope()),
            ).fetchone()
            if existing:
                raise DailyReportExistsError("当日车间报表已经生成")

            unique_records = _unique_snapshot_records(records, report_date)

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
                    work_time, production_time, remark, production_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        record.production_date.isoformat() if record.production_date else report_date,
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
        scope_machine_no: str | None = None,
    ) -> dict[str, Any] | None:
        selected_scope = self.require_scope()
        if scope_machine_no is not None and scope_machine_no != selected_scope:
            raise DailyReportScopeMismatchError("日报范围与设备上下文不一致")
        scope_machine_no = selected_scope
        with self.connect() as connection:
            self.begin_immediate(connection)
            report = connection.execute(
                "SELECT * FROM workshop_reports WHERE report_date = ? AND scope_machine_no = ? COLLATE NOCASE",
                (report_date, self.require_scope()),
            ).fetchone()
            if report is None:
                return None
            if report["source_mode"] != source_mode:
                raise DailyReportSourceMismatchError("日报数据源已变化，拒绝混合同一日报快照")
            records = _unique_snapshot_records(records, report_date)
            snapshot_revision = int(report["snapshot_revision_floor"])
            if int(report["item_count"]) > 0:
                existing_rows = {
                    str(row["task_id"]): dict(row)
                    for row in connection.execute(
                        "SELECT * FROM workshop_report_items WHERE report_id = ?",
                        (report["report_id"],),
                    ).fetchall()
                }
                existing_ids = set(existing_rows)
                unique_records: list[WorkshopRecord] = []
                mismatched_task_ids: list[str] = []
                project_backfills: list[tuple[str, str]] = []
                for record in records:
                    task_id = str(record.task_id)
                    expected = _snapshot_values(record, report_date)
                    if task_id in existing_rows:
                        existing = existing_rows[task_id]
                        if not _snapshot_values_equal(existing, expected):
                            # Only repair a missing identifier; task data and confirmations stay intact.
                            if (
                                not (existing.get("project_name") or "").strip()
                                and expected["project_name"]
                                and _snapshot_values_equal(existing, {
                                    field: value for field, value in expected.items() if field != "project_name"
                                })
                            ):
                                project_backfills.append((expected["project_name"], existing["report_item_id"]))
                            else:
                                mismatched_task_ids.append(task_id)
                        continue
                    unique_records.append(record)
                if mismatched_task_ids:
                    shown = "、".join(mismatched_task_ids[:10])
                    suffix = "等" if len(mismatched_task_ids) > 10 else ""
                    raise DailyReportRecordMismatchError(
                        f"日报任务与生产源数据不一致：{shown}{suffix}；请检查数据源或采集配置"
                    )
                if project_backfills:
                    connection.executemany(
                        "UPDATE workshop_report_items SET project_name = ? WHERE report_item_id = ?",
                        project_backfills,
                    )
                if unique_records:
                    ordinal = int(connection.execute(
                        "SELECT COALESCE(MAX(ordinal), 0) FROM workshop_report_items WHERE report_id = ?",
                        (report["report_id"],),
                    ).fetchone()[0])
                    connection.executemany(
                        """
                        INSERT INTO workshop_report_items (
                            report_item_id, report_id, task_id, ordinal, project_name,
                            segment_name, program_no, cut_length, groove_length,
                            work_time, production_time, remark, production_date, snapshot_revision
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                f"wdi_{uuid4().hex[:20]}", report["report_id"],
                                str(record.task_id), ordinal + index,
                                record.project_code or record.ship_no, record.segment_name,
                                record.program_no,
                                str(record.cut_length) if record.cut_length is not None else None,
                                str(record.groove_length) if record.groove_length is not None else None,
                                str(record.work_time) if record.work_time is not None else None,
                                record.production_time.isoformat() if record.production_time else None,
                                record.remark,
                                record.production_date.isoformat() if record.production_date else report_date,
                                snapshot_revision,
                            )
                            for index, record in enumerate(unique_records, start=1)
                        ],
                    )
                    connection.execute(
                        "UPDATE workshop_reports SET item_count = ?, generated_by_id = ?, generated_by_name = ?, generated_at = ? WHERE report_id = ?",
                        (len(existing_ids) + len(unique_records), user["user_id"], user["display_name"], now, report["report_id"]),
                    )
                populated = connection.execute(
                    """
                    SELECT r.*, COUNT(c.task_id) AS confirmed_count
                    FROM workshop_reports AS r
                    LEFT JOIN workshop_report_items AS i ON i.report_id = r.report_id
                    LEFT JOIN workshop_confirmations AS c
                        ON c.task_id = i.task_id
                        AND c.scope_machine_no = r.scope_machine_no COLLATE NOCASE
                        AND (c.report_id = r.report_id OR c.report_id IS NULL)
                    WHERE r.report_id = ? GROUP BY r.report_id
                    """, (report["report_id"],),
                ).fetchone()
                return self._report_dict(populated)

            unique_records = records
            connection.executemany(
                """
                INSERT INTO workshop_report_items (
                    report_item_id, report_id, task_id, ordinal, project_name,
                    segment_name, program_no, cut_length, groove_length,
                    work_time, production_time, remark, production_date, snapshot_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        record.production_date.isoformat() if record.production_date else report_date,
                        snapshot_revision,
                    )
                    for ordinal, record in enumerate(unique_records, start=1)
                ],
            )
            connection.execute(
                """
                UPDATE workshop_reports
                SET source_mode = ?, scope_machine_no = ?, item_count = ?, generated_by_id = ?,
                    generated_by_name = ?, generated_at = ?, snapshot_revision_floor = ?
                WHERE report_id = ?
                """,
                (
                    source_mode,
                    scope_machine_no,
                    len(unique_records),
                    user["user_id"],
                    user["display_name"],
                    now,
                    snapshot_revision,
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
        confirmations = ConfirmationRepository(self.database, connection=self._connection, scope_machine_no=self.scope_machine_no).get_confirmations(
            [str(row["task_id"]) for row in rows], report_id=report["report_id"]
        )
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
                    production_date=date.fromisoformat(row["production_date"] or report_date),
                    snapshot_revision=int(row["snapshot_revision"]),
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
