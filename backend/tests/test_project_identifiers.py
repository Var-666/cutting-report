from dataclasses import replace
from datetime import date
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.domain import ReportFilter
from app.services.query_service import QueryService


QUERY_DIR = Path(__file__).parents[1] / "app/database/queries"
SOURCES = ["summary_base", "workshop", "equipment", "leadership_report", "leadership_projects", "list_projects", "list_ships"]
ROWS = [(None, "CMYZ0170"), ("", " CMYZ0170 "), ("   ", "CMYZ0170"),
        (" NAME-P ", "OTHER-CODE"), ("NAME-ONLY", None), (None, None), (" ", " ")]
EXPECTED = ["CMYZ0170", "CMYZ0170", "CMYZ0170", "NAME-P", "NAME-ONLY", None, None]


def mapping_query(source):
    if not source.startswith("list_"):
        return (QUERY_DIR / f"{source}.sql").read_text(), "project_number" if source in SOURCES[:3] else "project_name"
    queries = []
    service = QueryService(replace(settings, data_source_mode="sqlserver", demo_mode=False))
    service.client = SimpleNamespace(fetch_all=lambda query, parameters: queries.append(query) or [])
    getattr(service, source)(ReportFilter(report_date=date(2026, 10, 29)))
    return queries[0], "project_code" if source == "list_projects" else "project_name"


def project_expression(query, alias):
    line = next(line for line in query.splitlines() if f" AS {alias}" in line)
    return re.sub(r"\bN'", "'", line.rsplit(f" AS {alias}", 1)[0].strip())


def source_database():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE task (PROJECT_NAME TEXT, PROJECT_CODE TEXT)")
    connection.executemany("INSERT INTO task VALUES (?, ?)", ROWS)
    return connection


@pytest.mark.parametrize("source", SOURCES)
def test_project_number_falls_back_to_code_without_replacing_existing_name(source):
    query, alias = mapping_query(source)
    expression = project_expression(query, alias)
    with source_database() as connection:
        numbers = [row[0] for row in connection.execute(f"SELECT {expression} FROM task AS t ORDER BY rowid")]
    expected = [value or "未标注工程" for value in EXPECTED] if source.startswith("leadership_") else EXPECTED
    assert numbers == expected
    if source in SOURCES[:3]:
        assert "source.project_number AS project_code" in query
        assert "source.project_number AS ship_no" in query


@pytest.mark.parametrize("source", SOURCES[:3])
@pytest.mark.parametrize("project,ship,expected_count", [(None, None, 7), ("CMYZ0170", None, 3),
    (None, "CMYZ0170", 3), ("CMYZ0170", "CMYZ0170", 3), ("OTHER-CODE", None, 0), ("NAME-P", "CMYZ0170", 0)])
def test_project_and_ship_filters_use_the_same_displayed_number(source, project, ship, expected_count):
    query, alias = mapping_query(source)
    expression = project_expression(query, alias)
    conditions = [line.strip().removeprefix("AND ") for line in query.splitlines() if "AND (? IS NULL" in line]
    assert len(conditions) == 2
    with source_database() as connection:
        rows = connection.execute(
            f"WITH task_source AS (SELECT {expression} AS project_number FROM task AS t) "
            f"SELECT source.project_number FROM task_source AS source WHERE {' AND '.join(conditions)}",
            (project, project, ship, ship),
        ).fetchall()
    assert len(rows) == expected_count
