from datetime import date
from dataclasses import replace
import re
from pathlib import Path

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.models.domain import ReportFilter
from app.services.equipment_service import mock_energy, sort_equipment_records
from app.services.leadership_report_service import LeadershipReportService
from app.services.query_service import QueryService
from app.services.report_analysis_service import ReportAnalysisService


def _services(tmp_path: Path):
    settings = Settings(
        project_root=tmp_path,
        demo_mode=True,
        data_source_mode="demo",
        llm_enabled=False,
    )
    settings.ensure_runtime_dirs()
    metadata = MetadataRepository(settings.metadata_db_path)
    metadata.initialize()
    return settings, ReportAnalysisService(settings, metadata)


def test_three_report_types_get_distinct_simulated_analysis(tmp_path: Path) -> None:
    settings, service = _services(tmp_path)
    selected_date = date(2026, 8, 18)
    leadership = LeadershipReportService(settings).fetch(selected_date)
    bundle = QueryService(settings).fetch_report_data(
        ReportFilter(report_date=selected_date)
    )

    leadership_analysis = service.analyze_leadership(leadership)
    workshop_analysis = service.analyze_workshop(
        selected_date.isoformat(),
        bundle.workshop_records,
        comparison_records=bundle.workshop_records,
        comparison_date="2026-08-17",
        year_ago_records=bundle.workshop_records,
        year_ago_date="2025-08-18",
    )
    equipment_records = sort_equipment_records(mock_energy(bundle.equipment_records))
    equipment_analysis = service.analyze_equipment(
        selected_date.isoformat(),
        equipment_records,
        energy_data_mode="mock",
        comparison_records=equipment_records,
        comparison_date="2026-08-17",
        year_ago_records=equipment_records,
        year_ago_date="2025-08-18",
    )

    assert leadership_analysis.status == "simulated"
    assert leadership_analysis.mode == "simulated"
    assert "2026-08" in leadership_analysis.overview
    assert "同比" in leadership_analysis.features
    assert "工程" in leadership_analysis.features
    assert "确认率" in workshop_analysis.features
    assert "未完成工人确认" in workshop_analysis.alerts
    assert "模拟数据" in equipment_analysis.limitations
    assert "不进行能耗异常判定" in equipment_analysis.features
    assert "能耗数据待核对" in equipment_analysis.alerts
    assert leadership_analysis.overview != workshop_analysis.overview
    assert workshop_analysis.overview != equipment_analysis.overview
    assert all(result.facts for result in (
        leadership_analysis,
        workshop_analysis,
        equipment_analysis,
    ))
    for result in (leadership_analysis, workshop_analysis, equipment_analysis):
        assert [fact["id"] for fact in result.facts] == [f"F{index:03}" for index in range(1, 7)]
        assert all(fact.get("id") and fact.get("source") for fact in result.facts)
        referenced = result.overview + result.features + result.alerts + result.suggestions
        assert set(re.findall(r"\[(F\d{3})\]", referenced)) <= {fact["id"] for fact in result.facts}


def test_same_analysis_input_uses_metadata_cache(tmp_path: Path) -> None:
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))

    first = service.analyze_leadership(report)
    second = service.analyze_leadership(report)

    assert first.cached is False
    assert second.cached is True
    assert second.generated_at == first.generated_at
    assert second.overview == first.overview


def test_empty_reports_keep_six_facts_without_inventing_comparisons(tmp_path):
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    empty_total = replace(report.current_month_total, task_count=0)
    report = replace(report, current_month_total=empty_total, monthly_records=[], daily_records=[], year_ago_total=None, project_distribution=[])
    results = [
        service.analyze_leadership(report),
        service.analyze_workshop("2026-08-01", []),
        service.analyze_equipment("2026-08-01", []),
    ]
    for result in results:
        assert len(result.facts) == 6
        references = set(re.findall(r"\[(F\d{3})\]", result.overview + result.features + result.alerts + result.suggestions))
        assert references <= {fact["id"] for fact in result.facts}
    leader_facts = {fact["id"]: fact["value"] for fact in results[0].facts}
    assert leader_facts["F004"].startswith("无法比较")
    assert leader_facts["F005"].startswith("无法比较")
    assert leader_facts["F006"] == "暂无工程数据"
    assert results[1].facts[3]["value"] == "暂无任务"
    assert results[2].facts[2]["value"].startswith("缺失")


def test_energy_quality_counts_a_task_once_even_with_multiple_issues(tmp_path):
    settings, service = _services(tmp_path)
    bundle = QueryService(settings).fetch_report_data(ReportFilter(report_date=date(2026, 8, 18)))
    row = replace(bundle.equipment_records[0], gas_consumption=None, electricity_consumption=None, gas_data_status="overlap", electricity_data_status="gap")
    result = service.analyze_equipment("2026-08-18", [row])
    quality = result.facts[-1]
    assert quality["id"] == "F006"
    assert quality["value"] == "1 项"
    assert "计量区间重叠 1 项" in quality["source"]
    assert "采样或读数缺口 1 项" in quality["source"]


def test_model_and_fallback_receive_the_same_six_facts(tmp_path, monkeypatch):
    settings, service = _services(tmp_path)
    service.settings = replace(settings, llm_enabled=True)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))
    contexts = []

    def model(report_type, context):
        contexts.append(context)
        result = service._simulate_leadership(context)
        result.mode = "model"
        return result

    monkeypatch.setattr(service.llm, "analyze_context", model)
    result = service.analyze_leadership(report)
    assert len(contexts[0]["facts"]) == len(result.facts) == 6
    assert result.mode == "model"

    def fail(*_args):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(service.llm, "analyze_context", fail)
    fallback = service.analyze_leadership(report, force=True)
    assert fallback.mode == "simulated_fallback"
    assert fallback.facts == result.facts
