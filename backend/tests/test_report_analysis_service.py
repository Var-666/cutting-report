from datetime import date
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
    assert "同比" in workshop_analysis.features
    assert "未完成工人确认" in workshop_analysis.alerts
    assert "模拟数据" in equipment_analysis.limitations
    assert "不进行能耗异常判定" in equipment_analysis.features
    assert "同比" in equipment_analysis.features
    assert leadership_analysis.overview != workshop_analysis.overview
    assert workshop_analysis.overview != equipment_analysis.overview
    assert all(result.facts for result in (
        leadership_analysis,
        workshop_analysis,
        equipment_analysis,
    ))
    for result in (leadership_analysis, workshop_analysis, equipment_analysis):
        assert all(fact.get("id") and fact.get("source") for fact in result.facts)
        referenced = result.overview + result.features + result.alerts + result.suggestions
        assert any(f"[{fact['id']}]" in referenced for fact in result.facts)


def test_same_analysis_input_uses_metadata_cache(tmp_path: Path) -> None:
    settings, service = _services(tmp_path)
    report = LeadershipReportService(settings).fetch(date(2026, 8, 1))

    first = service.analyze_leadership(report)
    second = service.analyze_leadership(report)

    assert first.cached is False
    assert second.cached is True
    assert second.generated_at == first.generated_at
    assert second.overview == first.overview
