import pytest

from app.config import settings
from app.services.sql_workbench_service import (
    SqlWorkbenchService,
    SqlWorkbenchValidationError,
)


@pytest.fixture
def workbench() -> SqlWorkbenchService:
    return SqlWorkbenchService(settings)


def test_select_is_allowed_and_formatted(workbench: SqlWorkbenchService) -> None:
    result = workbench.validate("select top (10) id, name from dbo.Task order by id desc")

    assert result.formatted_sql.startswith("SELECT")
    assert "TOP 10" in result.formatted_sql
    assert result.tables == ["dbo.Task"]
    assert "id" in result.columns


def test_cte_select_is_allowed(workbench: SqlWorkbenchService) -> None:
    result = workbench.validate(
        "WITH x AS (SELECT id FROM dbo.Task) SELECT id FROM x"
    )

    assert result.formatted_sql.startswith("WITH x AS")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dbo.Task",
        "UPDATE dbo.Task SET name = 'x'",
        "INSERT INTO dbo.Task(id) VALUES (1)",
        "DROP TABLE dbo.Task",
        "SELECT * INTO dbo.TaskCopy FROM dbo.Task",
        "SELECT 1; SELECT 2",
        "WAITFOR DELAY '00:00:10'",
        "SELECT * FROM OPENROWSET('provider', 'connection', 'query')",
    ],
)
def test_unsafe_or_multiple_statements_are_rejected(
    workbench: SqlWorkbenchService, sql: str
) -> None:
    with pytest.raises(SqlWorkbenchValidationError):
        workbench.validate(sql)
