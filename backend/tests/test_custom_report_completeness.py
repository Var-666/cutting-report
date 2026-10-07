from contextlib import closing, contextmanager
from datetime import date
import sqlite3

import pytest

from app.config import Settings
from app.database.excel_task import ExcelTaskDataSource
from app.database.sqlserver import SqlServerClient
from app.services.custom_report_service import CustomReportService
from app.services.report_deadline import report_deadline


QUERY = {"sql_text": "SELECT TASK_ID AS amount FROM dbo.task", "columns": [
    {"field": "amount", "label": "数量", "type": "number"},
]}
DEFINITION = {"visual_config": {
    "metrics": [{"field": "amount", "aggregation": aggregation} for aggregation in
                ("sum", "avg", "min", "max", "count")],
    "chart": {"value_fields": ["amount"]},
}}
TEMPLATE = {"config": {"primary_color": "blue", "accent_color": "orange"}}


def test_full_metrics_do_not_depend_on_display_limit(tmp_path):
    service = CustomReportService(Settings(project_root=tmp_path, report_max_detail_rows=2))
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
    service = CustomReportService(Settings(project_root=tmp_path, report_max_detail_rows=1))

    def interrupted():
        yield {"amount": 10}
        raise TimeoutError("source interrupted")

    with pytest.raises(TimeoutError):
        service.build_report_data(definition=DEFINITION, query_task=QUERY,
                                  template=TEMPLATE, rows=interrupted())


def test_full_metric_scan_observes_report_deadline(tmp_path):
    service = CustomReportService(Settings(project_root=tmp_path, report_max_detail_rows=2))
    rows = ({"amount": value} for value in range(200))

    with pytest.raises(TimeoutError):
        with report_deadline(0):
            service.build_report_data(
                definition=DEFINITION, query_task=QUERY, template=TEMPLATE, rows=rows
            )


def test_preview_closes_source_at_display_limit(tmp_path, monkeypatch):
    service = CustomReportService(Settings(project_root=tmp_path, demo_mode=False,
                                           data_source_mode="sqlserver", report_max_detail_rows=2))
    consumed = []
    closed = []

    def source(sql, values):
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
    client = SqlServerClient(Settings(project_root=tmp_path))
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


def test_excel_stream_aggregates_beyond_display_limit(tmp_path, monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE task(amount)")
    connection.executemany("INSERT INTO task VALUES (?)", [(i,) for i in range(1100)])
    source = ExcelTaskDataSource(tmp_path / "task.xlsx")
    monkeypatch.setattr(source, "_sqlite_connection", lambda: connection)
    service = CustomReportService(Settings(project_root=tmp_path, report_max_detail_rows=2))
    try:
        with closing(source.iter_select("SELECT amount FROM task", ())) as rows:
            result = service.build_report_data(definition=DEFINITION, query_task=QUERY,
                                              template=TEMPLATE, rows=rows)
        assert result["total_row_count"] == 1100
        assert result["metrics"][0]["value"] == sum(range(1100))
        assert len(result["rows"]) == 2
        assert connection.execute("SELECT COUNT(*) FROM task").fetchone()[0] == 1100
    finally:
        connection.close()
