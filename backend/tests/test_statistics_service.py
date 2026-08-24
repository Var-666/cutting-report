from decimal import Decimal

from app.models.domain import SummaryBaseRecord
from app.services.statistics_service import StatisticsService


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

