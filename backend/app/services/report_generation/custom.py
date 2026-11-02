"""Custom report content preparation using the pipeline's existing dependencies."""
from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.definitions import DefinitionRepository
from app.services.custom_report_service import CustomReportService
from app.services.report_generation.common import PreparedReport, report_meta


def prepare_report(
    job: dict, *,
    settings: Settings,
    definitions: DefinitionRepository,
    custom_report_service: CustomReportService,
    ensure_active: Callable[[], None],
    update_status: Callable[[str, str], None],
) -> PreparedReport:
    ensure_active()
    definition = definitions.get_definition(job["definition_id"])
    if not definition or not definition["enabled"] or definition["is_builtin"]:
        raise ValueError("自定义报表定义不存在或尚未启用")
    query_task = definitions.get_query_task(definition["query_task_id"])
    template = definitions.get_template(definition["template_id"])
    if not query_task or not query_task["enabled"] or not template or not template["enabled"]:
        raise ValueError("报表定义引用的查询任务或模板不可用")

    report_date = date.fromisoformat(job["report_date"])
    parameters = json.loads(job.get("parameters_json") or "{}")
    update_status("calculating", "正在查询并计算完整自定义指标")
    with closing(custom_report_service.iter_rows(
        query_task,
        report_date=report_date,
        period_type=definition["period_type"],
        project_code=job.get("project_code"),
        ship_no=job.get("ship_no"),
        parameters=parameters,
    )) as rows:
        custom = custom_report_service.build_report_data(
            definition=definition, query_task=query_task, template=template, rows=rows,
        )
    ensure_active()
    update_status("analyzing", "正在应用报表模板")
    generated_at = datetime.now(ZoneInfo(settings.timezone))
    report = {
        "meta": report_meta(
            settings=settings,
            job=job,
            report_type="custom",
            generated_at=generated_at,
            data_updated_at=None,
            title=definition["name"],
        ),
        "definition": definition,
        "query_task": {
            "query_task_id": query_task["query_task_id"],
            "name": query_task["name"],
            "version": query_task["version"],
        },
        "template_meta": {
            "template_id": template["template_id"],
            "name": template["name"],
            "version": template["version"],
        },
        "filters": {
            "report_date": job["report_date"],
            "project_code": job.get("project_code"),
            "ship_no": job.get("ship_no"),
            "parameters": parameters,
        },
        "custom": custom,
    }
    return PreparedReport(
        report=report,
        generated_at=generated_at,
        task_count=custom["total_row_count"],
        analysis_status="skipped",
    )
