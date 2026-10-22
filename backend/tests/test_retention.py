from __future__ import annotations

import threading
import pytest
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.database.alarms import AlarmRepository
from app.database.automation import AutomationRepository
from app.database.dispatch import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.metadata import MetadataRepository
from app.database.retention import RetentionRepository
from app.database.sqlite import SQLiteDatabase
from app.database.workshop import WorkshopRepository
from app.services.retention_service import RetentionService
from app.workers.retention_worker import RetentionWorker


TZ = ZoneInfo("Asia/Shanghai")
OLD = "2025-01-01T08:00:00+08:00"
RECENT = "2026-09-20T08:00:00+08:00"
NOW = datetime(2026, 9, 21, 8, 0, tzinfo=TZ)


def _repositories(tmp_path):
    database = SQLiteDatabase(tmp_path / "data" / "metadata.db")
    workshop = WorkshopRepository(database)
    metadata = MetadataRepository(database)
    automation = AutomationRepository(database)
    alarms = AlarmRepository(database)
    dispatches = DispatchRepository(database)
    downloads = DownloadRepository(database)
    retention = RetentionRepository(database)
    for repository in (
        workshop,
        metadata,
        automation,
        alarms,
        dispatches,
        downloads,
        retention,
    ):
        repository.initialize()
    admin = workshop.create_user(
        username="retention-admin",
        display_name="保留策略管理员",
        password="correct-horse-battery-staple",
        role="admin",
        now=OLD,
    )
    return (
        database,
        workshop,
        metadata,
        automation,
        alarms,
        dispatches,
        downloads,
        retention,
        admin,
    )


def _alarm(source_id: int, *, closed: bool) -> dict:
    return {
        "alarm_info_id": source_id,
        "machine_no": "20kw切割机",
        "start_time": OLD,
        "remove_time": OLD if closed else None,
        "alarm_desc": f"测试告警 {source_id}",
        "alarm_code": source_id,
        "alarm_class": "故障",
        "alarm_type": "CUTTER",
    }


def test_dispatch_summary_survives_history_retention_and_still_appears_in_file_list(tmp_path):
    from app.database.pdm import MockPdmRepository
    from app.services.cutting_dispatch_service import CuttingDispatchService
    from app.services.file_source import DisabledFileSource

    *_, dispatches, _downloads, retention, admin = _repositories(tmp_path)
    pdm = MockPdmRepository()
    source = pdm.files[0]
    task = dispatches.create(dict(
        document_id=source["document_id"], version_id=source["version_id"],
        project_id=source["project_id"], document_no=source["document_no"],
        relative_path="cut.GEN", machine_ip="10.1.1.1", quantity=1,
        status="sent", stage="complete", source_mode="http",
    ), user=admin, now=OLD)
    result = retention.cleanup_expired_records(
        report_cutoff=None, analysis_cutoff=None, alarm_cutoff=None,
        dispatch_cutoff=RECENT, audit_cutoff=None, queued_at=NOW.isoformat(), batch_size=100,
    )
    assert result["dispatches"] == 1
    assert dispatches.get(task["dispatch_id"]) is None
    service = CuttingDispatchService(settings, pdm, dispatches, DisabledFileSource())
    rows = service.list_files(source["project_id"], catalog_id=None, status="all",
                              keyword="", page=1, page_size=20)["items"]
    item = next(item for item in rows if item["document_id"] == source["document_id"])
    assert item["dispatch_status"] == {"status": "sent", "status_label": "已派发", "stage_label": "推送完成"}
    dispatches.initialize()
    assert dispatches.latest_for_files([(source["document_id"], source["version_id"])])


def test_retention_keeps_unresolved_remote_dispatches(tmp_path):
    *_, dispatches, downloads, retention, admin = _repositories(tmp_path)
    protected = []
    deletable = []
    for stage, remote_id in [("upload_unknown", None), ("assign_failed", "remote"),
        ("assign_unknown", "remote"), ("cancel_unknown", "remote"), ("created", None),
        ("upload_failed", None)]:
        item = dispatches.create(dict(document_id=f"document-{stage}", project_id="p",
            version_id="v", document_no="cut", relative_path="cut.GEN",
            machine_ip="10.1.1.1", quantity=1, status="push_failed", stage=stage,
            source_mode="http"), user=admin, now=OLD)
        if remote_id:
            dispatches.transition(item["dispatch_id"], expected={"push_failed"},
                status="push_failed", remote_task_id=remote_id, now=OLD)
        (deletable if stage == "upload_failed" else protected).append(item["dispatch_id"])
    for _ in range(2):
        retention.cleanup_expired_records(report_cutoff=None, analysis_cutoff=None,
            alarm_cutoff=None, dispatch_cutoff=RECENT, audit_cutoff=None,
            queued_at=NOW.isoformat(), batch_size=100)
    assert all(dispatches.get(identifier) for identifier in protected)
    assert all(dispatches.get(identifier) is None for identifier in deletable)


def test_alarm_retention_uses_occurrence_and_preserves_reports_with_unresolved_members(tmp_path):
    database, _workshop, _metadata, _automation, alarms, *_rest, retention, _admin = _repositories(tmp_path)
    cutoff = NOW - timedelta(days=30)
    # Old alarms may only have been discovered or resolved today. Neither date
    # should extend the requested retention period measured from occurrence.
    rows = [
        {**_alarm(1, closed=True), "remove_time": NOW.isoformat()},
        _alarm(2, closed=False),
        {**_alarm(3, closed=True), "start_time": RECENT},
        {**_alarm(4, closed=True), "start_time": cutoff.strftime("%Y-%m-%d %H:%M:%S")},
        {**_alarm(5, closed=True), "start_time": (cutoff - timedelta(seconds=1)).isoformat()},
    ]
    alarms.ingest(rows, now=NOW, cursor=5, debounce_minutes=10)
    ids = {item["first_alarm_id"]: item["incident_id"] for item in alarms.list_incidents(None)}
    cfg = replace(settings, project_root=tmp_path, alarm_retention_days=30,
                  report_retention_days=0, analysis_retention_days=0,
                  dispatch_retention_days=0, audit_retention_days=0)

    def report(identifier, members, *, failed=False):
        alarms.reserve_report(proposed_report_id=identifier, emission_key=identifier,
            machine_no="Cutter01", incident_ids=[ids[i] for i in members], now=OLD)
        if failed:
            alarms.fail_report(identifier, "生成失败后关联被解除")
            return
        html = cfg.reports_dir / "alarms" / f"{identifier}.html"
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text("report", encoding="utf-8")
        html.with_suffix(".pdf").write_bytes(b"pdf")
        alarms.complete_report(identifier, analysis={}, model_status="model",
            completed_at=NOW.isoformat(), html_relative_path=f"alarms/{identifier}.html",
            pdf_relative_path=f"alarms/{identifier}.pdf", pdf_filename=f"{identifier}.pdf",
            file_size=3, sha256="test")

    report("expired", [1, 5])
    report("failed-open", [2], failed=True)
    report("shared", [1, 2, 3])
    report("boundary", [4])
    result = RetentionService(cfg, retention).cleanup_expired(now=NOW)

    assert result["records"]["alarm_incidents"] == 2
    assert result["records"]["alarm_events"] == 2
    assert result["records"]["alarm_reports"] == 1
    assert result["files"] == {"deleted": 2, "missing": 0, "failed": 0}
    assert alarms.get_report("expired") is None
    assert {item["first_alarm_id"] for item in alarms.list_incidents(None)} == {2, 3, 4}
    for identifier in ("failed-open", "shared", "boundary"):
        assert alarms.get_report(identifier) is not None
    assert (cfg.reports_dir / "alarms/shared.pdf").is_file()
    assert not (cfg.reports_dir / "alarms/expired.pdf").exists()

    # A shared report is retained until every member is resolved and expired.
    with database.connect() as connection:
        connection.execute("UPDATE alarm_incidents SET status = 'closed'")
    later = RetentionService(cfg, retention).cleanup_expired(now=NOW + timedelta(days=31))
    assert later["records"]["alarm_reports"] == 3
    assert alarms.list_incidents(None) == []
    assert alarms.list_reports() == []


@pytest.mark.parametrize("delivery_status", ["pending", "unknown"])
def test_unified_retention_removes_only_expired_terminal_data(tmp_path, delivery_status) -> None:
    (
        database,
        workshop,
        metadata,
        automation,
        alarms,
        dispatches,
        downloads,
        retention,
        admin,
    ) = _repositories(tmp_path)
    cfg = replace(
        settings,
        project_root=tmp_path,
        report_retention_days=365,
        analysis_retention_days=90,
        alarm_retention_days=365,
        dispatch_retention_days=365,
        audit_retention_days=365,
        retention_cleanup_batch_size=100,
    )

    metadata.create_job(
        report_id="old-report",
        report_date="2025-01-01",
        project_code=None,
        ship_no=None,
        request_id="test-old",
        created_at=OLD,
    )
    metadata.update_status(
        "old-report",
        "completed",
        "完成",
        finished_at=OLD,
    )
    metadata.save_report(
        report_id="old-report",
        filename="old.html",
        relative_path="2025/01/old.html",
        file_size=4,
        sha256="old",
        task_count=1,
        generated_at=OLD,
        system_version="test",
    )
    old_html = cfg.reports_dir / "2025" / "01" / "old.html"
    old_pdf = old_html.with_suffix(".pdf")
    old_html.parent.mkdir(parents=True)
    old_html.write_text("old", encoding="utf-8")
    old_pdf.write_bytes(b"pdf")
    metadata.create_job(
        report_id="active-report",
        report_date="2025-01-01",
        project_code=None,
        ship_no=None,
        request_id="test-active",
        created_at=OLD,
    )

    metadata.save_analysis(
        cache_key="old-analysis",
        report_type="equipment",
        report_date="2025-01-01",
        input_hash="old",
        data_version="old",
        status="ok",
        mode="llm",
        model_name="test",
        prompt_version="v1",
        result={"summary": "old"},
        generated_at=OLD,
    )
    metadata.save_analysis(
        cache_key="recent-analysis",
        report_type="equipment",
        report_date="2026-09-20",
        input_hash="new",
        data_version="new",
        status="ok",
        mode="llm",
        model_name="test",
        prompt_version="v1",
        result={"summary": "recent"},
        generated_at=RECENT,
    )

    old_time = datetime.fromisoformat(OLD)
    alarms.ingest([_alarm(1, closed=True)], now=old_time, cursor=1, debounce_minutes=10)
    old_incident = alarms.list_incidents("closed")[0]
    alarm_report_id = alarms.reserve_report(
        proposed_report_id="old-alarm-report",
        emission_key="old-alarm",
        machine_no="20kw切割机",
        incident_ids=[old_incident["incident_id"]],
        now=OLD,
    )
    alarm_html = cfg.reports_dir / "alarms" / "2025" / "01" / "old.html"
    alarm_pdf = alarm_html.with_suffix(".pdf")
    alarm_html.parent.mkdir(parents=True)
    alarm_html.write_text("alarm", encoding="utf-8")
    alarm_pdf.write_bytes(b"alarm-pdf")
    alarms.complete_report(
        alarm_report_id,
        analysis={"summary": "old"},
        html_relative_path="alarms/2025/01/old.html",
        pdf_relative_path="alarms/2025/01/old.pdf",
        pdf_filename="old.pdf",
        file_size=9,
        sha256="alarm",
        model_status="ok",
        completed_at=OLD,
    )
    alarms.ingest(
        [_alarm(2, closed=False)],
        now=datetime.fromisoformat(RECENT),
        cursor=2,
        debounce_minutes=10,
    )
    open_incident = alarms.list_incidents("open")[0]
    active_alarm_report_id = alarms.reserve_report(
        proposed_report_id="active-alarm-report",
        emission_key="active-alarm",
        machine_no="20kw切割机",
        incident_ids=[open_incident["incident_id"]],
        now=OLD,
    )
    active_alarm_html = cfg.reports_dir / "alarms" / "active.html"
    active_alarm_pdf = active_alarm_html.with_suffix(".pdf")
    active_alarm_html.parent.mkdir(parents=True, exist_ok=True)
    active_alarm_html.write_text("active", encoding="utf-8")
    active_alarm_pdf.write_bytes(b"active-pdf")
    alarms.complete_report(
        active_alarm_report_id,
        analysis={"summary": "active"},
        html_relative_path="alarms/active.html",
        pdf_relative_path="alarms/active.pdf",
        pdf_filename="active.pdf",
        file_size=10,
        sha256="active",
        model_status="ok",
        completed_at=OLD,
    )

    dispatch_data = {
        "document_id": "doc-old",
        "project_id": "project",
        "project_name": "项目",
        "catalog_id": None,
        "catalog_title": None,
        "version_id": "version",
        "version": "1",
        "document_no": "old.GEN",
        "relative_path": "old.GEN",
        "machine_ip": "10.1.1.1",
        "material": "AH36",
        "thickness": 10,
        "quantity": 1,
        "status": "sent",
        "source_mode": "http",
    }
    old_dispatch = dispatches.create(dispatch_data, user=admin, now=OLD)
    active_dispatch = dispatches.create(
        {**dispatch_data, "document_id": "doc-active", "status": "ready"},
        user=admin,
        now=OLD,
    )
    downloads.save(
        {
            "download_id": f"dl_{'a' * 32}",
            "document_id": "doc-old",
            "status": "downloaded",
            "created_at": OLD,
            "updated_at": OLD,
            "file_deleted_at": OLD,
        }
    )

    with database.connect() as connection:
        for report_id, report_date, task_id, generated_at in (
            ("workshop-old", "2025-01-01", "task-old", OLD),
            ("workshop-recent", "2026-09-20", "task-recent", RECENT),
        ):
            connection.execute(
                """
                INSERT INTO workshop_reports (
                    report_id, report_date, source_mode, scope_machine_no,
                    item_count, generated_by_id, generated_by_name,
                    generated_at, created_at
                ) VALUES (?, ?, 'database', 'Cutter01', 1, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    report_date,
                    admin["user_id"],
                    admin["display_name"],
                    generated_at,
                    generated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO workshop_report_items (
                    report_item_id, report_id, task_id, ordinal
                ) VALUES (?, ?, ?, 1)
                """,
                (f"item-{task_id}", report_id, task_id),
            )
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
                    admin["user_id"],
                    admin["display_name"],
                    generated_at,
                    generated_at,
                    generated_at,
                ),
            )
        connection.execute(
            """
            INSERT INTO workshop_report_items (
                report_item_id, report_id, task_id, ordinal
            ) VALUES ('item-task-shared-old', 'workshop-old', 'task-shared', 2),
                     ('item-task-shared-recent', 'workshop-recent', 'task-shared', 2)
            """
        )
        connection.execute(
            """
            UPDATE workshop_reports SET item_count = 2
            WHERE report_id IN ('workshop-old', 'workshop-recent')
            """
        )
        connection.execute(
            """
            INSERT INTO workshop_confirmations (
                task_id, report_id, worker_id, worker_name,
                confirmed_at, created_at, updated_at
            ) VALUES ('task-shared', 'workshop-old', ?, ?, ?, ?, ?)
            """,
            (admin["user_id"], admin["display_name"], OLD, OLD, OLD),
        )

    schedule = automation.create_schedule(
        {
            "name": "旧任务",
            "report_type": "equipment",
            "frequency": "daily",
            "run_time": "08:00",
            "weekday": None,
            "month_day": None,
            "report_period": "current_day",
            "recipients": ["test@example.com"],
            "subject_template": "测试",
            "chart_type": "bar_line",
            "attach_report": True,
            "max_attempts": 1,
            "enabled": False,
            "next_run_at": RECENT,
        },
        user=admin,
        now=OLD,
    )
    old_run = automation.reserve_run(
        schedule,
        scheduled_for=OLD,
        report_date="2025-01-01",
        trigger_type="manual",
        now=OLD,
    )
    assert old_run is not None
    automation.mark_run(old_run["run_id"], "failed", now=OLD, error="old")

    metadata.create_job(
        report_id="pending-delivery-report",
        report_date="2025-01-02",
        project_code=None,
        ship_no=None,
        request_id="test-pending-delivery",
        created_at=OLD,
    )
    metadata.update_status(
        "pending-delivery-report",
        "completed",
        "完成",
        finished_at=OLD,
    )
    metadata.save_report(
        report_id="pending-delivery-report",
        filename="pending.html",
        relative_path="2025/01/pending.html",
        file_size=7,
        sha256="pending",
        task_count=1,
        generated_at=OLD,
        system_version="test",
    )
    pending_file = cfg.reports_dir / "2025" / "01" / "pending.html"
    pending_file.write_text("pending", encoding="utf-8")
    pending_run = automation.reserve_run(
        schedule,
        scheduled_for="2025-01-02T08:00:00+08:00",
        report_date="2025-01-02",
        trigger_type="manual",
        now=OLD,
    )
    assert pending_run is not None
    automation.attach_report(
        pending_run["run_id"],
        "pending-delivery-report",
        now=OLD,
    )
    assert automation.enqueue_delivery(
        "pending-delivery-report",
        subject="待发送",
        now=OLD,
    ) is not None
    if delivery_status == "unknown":
        delivery = automation.claim_delivery(OLD)
        automation.mark_delivery_unknown(delivery, now=OLD)

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO workshop_confirmation_audits (
                audit_id, task_id, action, reason, operated_by_id,
                operated_by_name, operated_by_role, operated_at
            ) VALUES ('audit-old', 'task-old', 'admin_revoke', 'old', ?, ?, 'admin', ?)
            """,
            (admin["user_id"], admin["display_name"], OLD),
        )
        connection.execute(
            """
            INSERT INTO workshop_confirmation_audits (
                audit_id, task_id, action, reason, operated_by_id,
                operated_by_name, operated_by_role, operated_at
            ) VALUES ('audit-recent', 'task-recent', 'admin_revoke', 'recent', ?, ?, 'admin', ?)
            """,
            (admin["user_id"], admin["display_name"], RECENT),
        )
        connection.execute(
            """
            INSERT INTO admin_role_audits (
                audit_id, subject_user_id, subject_external_id, action,
                operated_by_id, operated_by_name, operated_at
            ) VALUES ('admin-audit-old', ?, 'external-old', 'grant', ?, ?, ?)
            """,
            (admin["user_id"], admin["user_id"], admin["display_name"], OLD),
        )

    result = RetentionService(cfg, retention).cleanup_expired(now=NOW)
    assert result["records"] == {
        "reports": 1,
        "automation_runs": 1,
        "workshop_reports": 1,
        "workshop_items": 2,
        "workshop_confirmations": 1,
        "analyses": 1,
        "alarm_reports": 1,
        "alarm_incidents": 1,
        "alarm_events": 1,
        "dispatches": 1,
        "download_records": 1,
        "confirmation_audits": 1,
        "admin_audits": 1,
    }
    assert result["files"] == {"deleted": 4, "missing": 0, "failed": 0}
    assert metadata.get_job("old-report") is None
    assert metadata.get_job("active-report") is not None
    assert metadata.get_job("pending-delivery-report") is not None
    assert pending_file.is_file()
    assert not old_html.exists() and not old_pdf.exists()
    assert not alarm_html.exists() and not alarm_pdf.exists()
    assert active_alarm_html.is_file() and active_alarm_pdf.is_file()
    assert alarms.get_report("active-alarm-report") is not None
    assert metadata.get_analysis(
        "recent-analysis",
        report_type="equipment",
        report_date="2026-09-20",
        data_version="new",
    ) is not None
    assert alarms.list_incidents("open")[0]["latest_alarm_id"] == 2
    assert dispatches.get(old_dispatch["dispatch_id"]) is None
    assert dispatches.get(active_dispatch["dispatch_id"]) is not None
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workshop_confirmation_audits WHERE audit_id='audit-recent'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM workshop_reports WHERE report_id='workshop-old'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM workshop_confirmations WHERE task_id='task-old'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM workshop_reports WHERE report_id='workshop-recent'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM workshop_confirmations WHERE task_id='task-recent'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM workshop_confirmations WHERE task_id='task-shared'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM retention_file_deletions"
        ).fetchone()[0] == 0

    repeated = RetentionService(cfg, retention).cleanup_expired(now=NOW)
    assert not any(repeated["records"].values())
    assert repeated["files"] == {"deleted": 0, "missing": 0, "failed": 0}


def test_retention_file_queue_retries_unsafe_paths_without_escaping(tmp_path) -> None:
    database, *_items, retention, _admin = _repositories(tmp_path)
    external = tmp_path / "outside.txt"
    external.write_text("keep", encoding="utf-8")
    report_link = tmp_path / "reports" / "outside-link.txt"
    report_link.parent.mkdir(parents=True)
    report_link.symlink_to(external)
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO retention_file_deletions (
                relative_path, category, queued_at, updated_at
            ) VALUES (?, 'report', ?, ?)
            """,
            (
                ("../outside.txt", OLD, OLD),
                ("outside-link.txt", OLD, OLD),
            ),
        )
    cfg = replace(
        settings,
        project_root=tmp_path,
        report_retention_days=0,
        analysis_retention_days=0,
        alarm_retention_days=0,
        dispatch_retention_days=0,
        audit_retention_days=0,
    )

    result = RetentionService(cfg, retention).cleanup_expired(now=NOW)

    assert result["files"] == {"deleted": 1, "missing": 0, "failed": 1}
    assert external.read_text(encoding="utf-8") == "keep"
    assert not report_link.exists()
    queued = retention.list_file_deletions(10)
    assert len(queued) == 1
    assert queued[0]["attempt_count"] == 1
    assert queued[0]["last_error"] == "报告清理路径无效"


def test_retention_worker_runs_immediately_and_stops_cleanly() -> None:
    completed = threading.Event()

    class Service:
        calls = 0
        pending = False

        def has_pending_file_deletions(self):
            return self.pending

        def cleanup_expired(self):
            self.calls += 1
            completed.set()
            return {"skipped": False, "records": {}, "files": {}}

    service = Service()
    worker = RetentionWorker(settings, service)
    worker.start()
    assert completed.wait(1)
    worker.stop()
    assert service.calls == 1
    assert not worker.thread.is_alive()

    disabled = RetentionWorker(
        replace(
            settings,
            report_retention_days=0,
            analysis_retention_days=0,
            alarm_retention_days=0,
            dispatch_retention_days=0,
            audit_retention_days=0,
        ),
        service,
    )
    disabled.start()
    assert not disabled.thread.is_alive()

    service.pending = True
    assert RetentionWorker(disabled.settings, service).enabled
