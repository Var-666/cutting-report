from contextlib import closing, contextmanager
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from tests.line_fixtures import cutting_settings, source_client
from app.database.sqlserver import SqlServerClient
from app.modules.reports.custom import CustomReportService
from app.modules.reports.generation.custom import prepare_report
from app.modules.reports.service import ReportService
from app.models.designer import TemplateConfig
from app.core.report_deadline import report_deadline


QUERY = {"sql_text": "SELECT TASK_ID AS amount FROM dbo.task WHERE MACHINE_NO = :machine_no", "columns": [
    {"field": "amount", "label": "数量", "type": "number"},
]}
DEFINITION = {"visual_config": {
    "metrics": [{"field": "amount", "aggregation": aggregation} for aggregation in
                ("sum", "avg", "min", "max", "count")],
    "chart": {"value_fields": ["amount"]},
}}
TEMPLATE = {"config": {"primary_color": "blue", "accent_color": "orange"}}


@pytest.mark.parametrize("period_type", ["day", "month"])
@pytest.mark.parametrize("explicit_range", [False, True])
def test_custom_report_query_and_display_use_the_same_period(tmp_path, period_type, explicit_range):
    settings = cutting_settings(project_root=tmp_path, data_source_mode="sqlserver", demo_mode=False)
    service = CustomReportService(settings, client=source_client(settings))
    query = {**QUERY, "enabled": True, "query_task_id": "q", "name": "产量", "version": 1,
             "sql_text": "SELECT TASK_ID AS amount FROM dbo.task WHERE MACHINE_NO = :machine_no "
                         "AND END_TIME >= :period_start AND END_TIME < :period_end"}
    definition = {**DEFINITION, "enabled": True, "is_builtin": False, "name": "自定义产量",
                  "query_task_id": "q", "template_id": "t", "period_type": period_type}
    template = {"config": TemplateConfig().model_dump(), "enabled": True, "template_id": "t", "name": "模板", "version": 1}
    start, end = date(2026, 11, 1), date(2026, 11, 3)
    job = {"report_id": "custom-period", "report_type": "custom", "definition_id": "custom",
           "report_date": start.isoformat()}
    if explicit_range:
        job.update(period_start=start.isoformat(), period_end=end.isoformat(),
                   parameters_json=json.dumps({"period_start": "2025-01-01", "period_end": "2025-01-02"}))
    expected_end = end + timedelta(days=1) if explicit_range else date(2026, 12, 1) if period_type == "month" else start + timedelta(days=1)
    source_days = [date(2026, 10, 31), start, date(2026, 11, 2), end, date(2026, 12, 1)]
    calls = []

    def rows(sql, values):
        calls.append(values)
        machine, first, last = values
        assert machine == settings.report_machine_no
        return iter({"amount": 1} for day in source_days if first <= day < last)

    service.client.iter_rows = rows
    prepared = prepare_report(job, settings=settings, custom_report_service=service,
        definitions=SimpleNamespace(get_definition=lambda _: definition, get_query_task=lambda _: query,
                                    get_template=lambda _: template),
        ensure_active=lambda: None, update_status=lambda *_: None)
    assert calls == [(settings.report_machine_no, start, expected_end)]
    assert prepared.task_count == (3 if explicit_range or period_type == "month" else 1)
    html = ReportService(settings).environment.get_template("custom_report.html.j2").render(
        report=prepared.report, device_name="测试切割机", demo_mode=False, echarts="")
    if explicit_range:
        assert prepared.report["filters"]["period_start"] == start.isoformat()
        assert prepared.report["filters"]["period_end"] == end.isoformat()
        assert "2026-11-01 至 2026-11-03" in html
    else:
        assert "2026-11-01" in html


@pytest.mark.parametrize(("start", "end", "message"), [
    (date(2026, 11, 1), None, "必须同时提供"),
    (date(2026, 11, 3), date(2026, 11, 1), "不能早于"),
    (date(2025, 1, 1), date(2026, 1, 2), "最长为"),
])
def test_custom_range_validation_precedes_source_query(tmp_path, start, end, message):
    service = CustomReportService(cutting_settings(project_root=tmp_path), client=None)
    with pytest.raises(ValueError, match=message):
        list(service.iter_rows(QUERY, report_date=date(2026, 11, 1), period_type="day",
                               period_start=start, period_end=end))


@pytest.mark.parametrize(("start", "end"), [
    (date(2026, 10, 31), date(2026, 11, 1)),
    (date(2024, 2, 28), date(2024, 3, 1)),
])
def test_custom_range_handles_month_boundaries_and_requires_query_bounds(tmp_path, start, end):
    settings = cutting_settings(project_root=tmp_path, data_source_mode="sqlserver", demo_mode=False)
    service = CustomReportService(settings, client=source_client(settings))
    seen = []
    service.client.iter_rows = lambda sql, values: (seen.append(values) or (row for row in ()))
    query = {**QUERY, "sql_text": QUERY["sql_text"] + " AND END_TIME >= :period_start AND END_TIME < :period_end"}
    list(service.iter_rows(query, report_date=start, period_type="day", period_start=start, period_end=end))
    assert seen == [(settings.report_machine_no, start, end + timedelta(days=1))]
    with pytest.raises(ValueError, match="必须同时声明"):
        list(service.iter_rows(QUERY, report_date=start, period_type="day", period_start=start, period_end=end))
    assert len(seen) == 1


def test_full_metrics_do_not_depend_on_display_limit(tmp_path):
    service = CustomReportService(cutting_settings(project_root=tmp_path, report_max_detail_rows=2), client=source_client(cutting_settings(project_root=tmp_path, report_max_detail_rows=2)))
    result = service.build_report_data(
        definition=DEFINITION, query_task=QUERY, template=TEMPLATE,
        rows=({"amount": value} for value in [1, 2, 300, None, "invalid"]),
    )
    assert [metric["value"] for metric in result["metrics"]] == [303, 101, 1, 300, 5]
    assert result["total_row_count"] == 5
    assert result["truncated"] is True
    assert result["rows"] == [{"amount": 1}, {"amount": 2}]
    assert result["chart"]["series"][0]["data"] == [1, 2]


def test_partial_query_failure_cannot_publish_partial_totals(tmp_path):
    service = CustomReportService(cutting_settings(project_root=tmp_path, report_max_detail_rows=1), client=source_client(cutting_settings(project_root=tmp_path, report_max_detail_rows=1)))

    def interrupted():
        yield {"amount": 10}
        raise TimeoutError("source interrupted")

    with pytest.raises(TimeoutError):
        service.build_report_data(definition=DEFINITION, query_task=QUERY,
                                  template=TEMPLATE, rows=interrupted())


def test_full_metric_scan_observes_report_deadline(tmp_path):
    service = CustomReportService(cutting_settings(project_root=tmp_path, report_max_detail_rows=2), client=source_client(cutting_settings(project_root=tmp_path, report_max_detail_rows=2)))
    rows = ({"amount": value} for value in range(200))

    with pytest.raises(TimeoutError):
        with report_deadline(0):
            service.build_report_data(
                definition=DEFINITION, query_task=QUERY, template=TEMPLATE, rows=rows
            )


def test_preview_closes_source_at_display_limit(tmp_path, monkeypatch):
    service = CustomReportService(cutting_settings(project_root=tmp_path, demo_mode=False,
                                           data_source_mode="sqlserver", report_max_detail_rows=2), client=source_client(cutting_settings(project_root=tmp_path, demo_mode=False,
                                           data_source_mode="sqlserver", report_max_detail_rows=2)))
    consumed = []
    closed = []

    def source(sql, values):
        assert "MACHINE_NO = ?" in sql and values == (service.settings.report_machine_no,)
        try:
            for value in range(1000):
                consumed.append(value)
                yield {"amount": value}
        finally:
            closed.append(True)

    monkeypatch.setattr(service.client, "iter_rows", source)
    assert len(service.execute(QUERY, report_date=date(2026, 9, 1), period_type="day")) == 2
    assert consumed == [0, 1]
    assert closed == [True]


@pytest.mark.parametrize("early_exit", [False, True])
def test_sqlserver_stream_uses_batches_and_releases_cursor(tmp_path, monkeypatch, early_exit):
    client = source_client(cutting_settings(project_root=tmp_path))
    events = []

    class Cursor:
        description = [("amount",)]
        batches = iter([[(1,), (2,)], [(3,)], []])

        def execute(self, sql, values):
            events.append("execute")

        def fetchmany(self, size):
            assert size == 500
            return next(self.batches)

        def close(self):
            events.append("cursor closed")

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        try:
            yield Connection()
        finally:
            events.append("connection closed")

    monkeypatch.setattr(client, "connect", connect)
    with closing(client.iter_rows("SELECT amount", ())) as rows:
        if early_exit:
            assert next(rows) == {"amount": 1}
        else:
            assert list(rows) == [{"amount": 1}, {"amount": 2}, {"amount": 3}]
    assert events == ["execute", "cursor closed", "connection closed"]


def test_sqlserver_report_aggregates_full_stream_beyond_display_limit(tmp_path, monkeypatch):
    service = CustomReportService(cutting_settings(project_root=tmp_path, data_source_mode="sqlserver", report_max_detail_rows=2), client=source_client(cutting_settings(project_root=tmp_path, data_source_mode="sqlserver", report_max_detail_rows=2)))
    closed = []

    def source(sql, values):
        assert "dbo.task" in sql
        assert "MACHINE_NO = ?" in sql and values == (service.settings.report_machine_no,)
        try:
            yield from ({"amount": i} for i in range(1100))
        finally:
            closed.append(True)

    monkeypatch.setattr(service.client, "iter_rows", source)
    with closing(service.iter_rows(QUERY, report_date=date(2026, 9, 1), period_type="day")) as rows:
        result = service.build_report_data(definition=DEFINITION, query_task=QUERY,
                                          template=TEMPLATE, rows=rows)
    assert result["total_row_count"] == 1100
    assert result["metrics"][0]["value"] == sum(range(1100))
    assert len(result["rows"]) == 2
    assert closed == [True]
