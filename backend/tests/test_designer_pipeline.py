from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.definitions import DefinitionRepository
from app.database.metadata import MetadataRepository
from app.services.custom_report_service import CustomReportService
from app.services.pipeline import ReportPipeline


QUERY_PAYLOAD = {
    "code": "daily_output",
    "name": "每日产量查询",
    "description": "按日期查询产量",
    "sql_text": """
        SELECT OP_DATE AS production_date, TASK_ID AS task_id,
               CUT_LENGTH AS cut_length
        FROM dbo.task
        WHERE OP_DATE >= :period_start AND OP_DATE < :period_end
    """,
    "columns": [
        {"field": "production_date", "label": "日期", "type": "date", "unit": ""},
        {"field": "task_id", "label": "任务号", "type": "text", "unit": ""},
        {"field": "cut_length", "label": "切割长度", "type": "number", "unit": "米"},
    ],
    "enabled": True,
}


def create_definition(repository: DefinitionRepository) -> dict:
    query = repository.save_query_task(QUERY_PAYLOAD)
    return repository.save_definition(
        {
            "code": "daily_output_report",
            "name": "每日产量自定义报表",
            "description": "配置驱动的产量报表",
            "query_task_id": query["query_task_id"],
            "template_id": "tpl_classic_blue",
            "period_type": "day",
            "visual_config": {
                "columns": ["production_date", "task_id", "cut_length"],
                "metrics": [
                    {"field": "cut_length", "label": "总切割长度", "aggregation": "sum", "unit": "米"}
                ],
                "chart": {
                    "type": "bar",
                    "category_field": "production_date",
                    "value_fields": ["cut_length"],
                    "title": "每日切割长度",
                },
            },
            "enabled": True,
        }
    )


def test_definition_repository_seeds_builtins_and_versions_updates(tmp_path: Path) -> None:
    repository = DefinitionRepository(tmp_path / "metadata.db")
    repository.initialize()

    assert [item["code"] for item in repository.list_definitions()] == [
        "leadership",
        "workshop",
        "equipment",
    ]
    definition = create_definition(repository)
    query = repository.get_query_task(definition["query_task_id"])
    query["name"] = "每日产量查询（更新）"
    updated = repository.save_query_task(query, query["query_task_id"])

    assert updated["version"] == 2
    assert repository.list_definitions(enabled_only=True, include_builtin=False)[0]["code"] == "daily_output_report"


def test_custom_query_named_parameters_are_compiled_safely(tmp_path: Path) -> None:
    service = CustomReportService(
        Settings(project_root=tmp_path, demo_mode=False, data_source_mode="sqlserver")
    )
    sql, values = service._compile(
        QUERY_PAYLOAD["sql_text"],
        report_date=datetime(2026, 8, 18).date(),
        period_type="day",
        project_code=None,
        ship_no=None,
        parameters={},
    )

    assert ":period_start" not in sql
    assert sql.count("?") == 2
    assert [value.isoformat() for value in values] == ["2026-08-18", "2026-08-19"]


def test_custom_demo_pipeline_generates_configured_report(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "echarts.min.js").write_text(
        "window.echarts={init:function(){return {setOption:function(){},resize:function(){}}}};",
        encoding="utf-8",
    )
    settings = Settings(
        project_root=tmp_path,
        demo_mode=True,
        data_source_mode="demo",
        app_version="test",
    )
    settings.ensure_runtime_dirs()
    metadata = MetadataRepository(settings.metadata_db_path)
    metadata.initialize()
    definitions = DefinitionRepository(settings.metadata_db_path, settings.timezone)
    definitions.initialize()
    definition = create_definition(definitions)
    now = datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")
    metadata.create_job(
        report_id="rpt_custom_test",
        report_date="2026-08-18",
        project_code=None,
        ship_no=None,
        request_id="req_custom",
        created_at=now,
        report_type="custom",
        definition_id=definition["definition_id"],
        report_title=definition["name"],
    )
    job = metadata.claim_next_job(now)
    assert job is not None

    ReportPipeline(settings, metadata, definitions).run(job)

    completed = metadata.get_job("rpt_custom_test")
    assert completed["status"] == "completed"
    assert completed["task_count"] == 10
    html = (settings.reports_dir / completed["relative_path"]).read_text(encoding="utf-8")
    assert "每日产量自定义报表" in html
    assert "每日切割长度" in html
    assert 'id="custom-chart"' in html
    assert "报告结束" in html
    assert "@page { size:A4 landscape" in html
    assert "window.__REPORT_READY__ = true" in html
    assert "report-printing" in html
    assert 'class="screen-only"' in html
    assert 'class="print-table-page"' in html
    assert 'class="print-table-chunk"' in html
