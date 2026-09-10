from datetime import datetime
from decimal import Decimal

from app.models.domain import SummaryBaseRecord, WorkshopRecord
from app.services.statistics_service import StatisticsService, build_worker_work_summaries


def record(
    task_id: str,
    cut: str | None,
    groove: str | None,
    work: str | None,
) -> SummaryBaseRecord:
    return SummaryBaseRecord(
        task_id=task_id,
        project_code="A001",
        ship_no="S001",
        segment_name="P01",
        program_no=f"OP-{task_id}",
        cut_length=Decimal(cut) if cut is not None else None,
        groove_length=Decimal(groove) if groove is not None else None,
        work_time=Decimal(work) if work is not None else None,
    )


def test_calculate_core_metrics_and_null_rules() -> None:
    result = StatisticsService().calculate(
        [
            record("1", "100", "20", "10"),
            record("2", "200", None, "30"),
            record("3", None, "40", None),
        ]
    )

    assert result.summary.task_count == 3
    assert result.summary.total_cut_length == Decimal("300")
    assert result.summary.total_groove_length == Decimal("60")
    assert result.summary.total_work_time == Decimal("40")
    assert result.summary.avg_cut_length == Decimal("150")
    assert result.summary.avg_groove_length == Decimal("30")
    assert result.summary.avg_work_time == Decimal("20")
    assert result.summary.min_cut_length == Decimal("100")
    assert result.summary.max_cut_length == Decimal("200")


def test_duplicate_task_is_not_counted_twice() -> None:
    result = StatisticsService().calculate(
        [record("1", "100", "20", "10"), record("1", "999", "999", "999")]
    )

    assert result.summary.task_count == 1
    assert result.summary.total_cut_length == Decimal("100")
    assert any("重复任务" in item.message for item in result.data_quality)


def test_negative_values_are_excluded_per_field() -> None:
    result = StatisticsService().calculate([record("1", "-1", "20", "10")])

    assert result.summary.task_count == 1
    assert result.summary.total_cut_length == Decimal("0")
    assert result.summary.total_groove_length == Decimal("20")
    assert any("负数数据" in item.message for item in result.data_quality)


def test_worker_work_summary_uses_only_confirmed_tasks_and_company_numbers() -> None:
    def workshop_record(
        task_id: str,
        work_time: str,
        worker_id: str | None,
        worker_name: str | None,
        company_no: str | None,
        confirmed_at: str | None,
    ) -> WorkshopRecord:
        return WorkshopRecord(
            task_id=task_id,
            project_code="A001",
            ship_no="S001",
            segment_name="P01",
            program_no=f"OP-{task_id}",
            cut_length=Decimal("10.5"),
            groove_length=Decimal("2.5"),
            work_time=Decimal(work_time),
            confirmed_by_id=worker_id,
            confirmed_by=worker_name,
            confirmed_by_company_no=company_no,
            confirmed_at=datetime.fromisoformat(confirmed_at) if confirmed_at else None,
        )

    summaries = build_worker_work_summaries(
        [
            workshop_record("1", "60", "worker-a", "张三", "C001", "2026-09-01T09:00:00+08:00"),
            workshop_record("2", "120", "worker-a", "张三", "C001", "2026-09-02T10:00:00+08:00"),
            workshop_record("3", "60", "worker-b", "李四", "C002", "2026-09-02T09:00:00+08:00"),
            workshop_record("4", "999", None, None, None, None),
        ]
    )

    assert [item["company_no"] for item in summaries] == ["C001", "C002"]
    assert summaries[0]["confirmed_task_count"] == 2
    assert summaries[0]["total_cut_length"] == Decimal("21.0")
    assert summaries[0]["total_work_time_seconds"] == Decimal("180")
    assert summaries[0]["work_time_share"] == 75.0
    assert summaries[0]["last_confirmed_at"] == datetime.fromisoformat(
        "2026-09-02T10:00:00+08:00"
    )
