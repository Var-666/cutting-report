from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlglot import exp, parse, transpile
from sqlglot.errors import ParseError

from app.config import Settings
from app.database.excel_task import ExcelTaskDataSource
from app.database.sqlserver import SqlServerClient


PROHIBITED_NODE_KEYS = {
    "alter",
    "command",
    "commit",
    "create",
    "delete",
    "drop",
    "execute",
    "grant",
    "insert",
    "into",
    "merge",
    "revoke",
    "rollback",
    "set",
    "transaction",
    "truncate",
    "update",
    "use",
}

PROHIBITED_SQL_NAMES = {
    "BULK",
    "OPENQUERY",
    "OPENROWSET",
    "OPENDATASOURCE",
    "WAITFOR",
    "XP_CMDSHELL",
}


class SqlWorkbenchValidationError(ValueError):
    pass


@dataclass(slots=True)
class ValidatedSql:
    formatted_sql: str
    tables: list[str]
    columns: list[str]


@dataclass(slots=True)
class QueryResult:
    columns: list[str]
    items: list[dict[str, Any]]
    returned: int
    truncated: bool
    elapsed_ms: float


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time, UUID)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _unique_columns(description: Any) -> list[str]:
    names: list[str] = []
    counts: dict[str, int] = {}
    for item in description:
        base = str(item[0] or "column")
        counts[base] = counts.get(base, 0) + 1
        names.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return names


class SqlWorkbenchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = SqlServerClient(settings)

    def validate(self, sql: str) -> ValidatedSql:
        cleaned = sql.strip()
        if not cleaned:
            raise SqlWorkbenchValidationError("SQL 不能为空")
        try:
            statements = [statement for statement in parse(cleaned, read="tsql") if statement]
        except ParseError as exc:
            raise SqlWorkbenchValidationError(f"T-SQL 语法解析失败：{exc}") from exc
        if len(statements) != 1:
            raise SqlWorkbenchValidationError("每次只能执行一条 SQL 语句")

        statement = statements[0]
        if not isinstance(statement, exp.Query):
            raise SqlWorkbenchValidationError("SQL 工作台只允许 SELECT 或 WITH...SELECT 查询")

        for node in statement.walk():
            if node.key.lower() in PROHIBITED_NODE_KEYS:
                raise SqlWorkbenchValidationError(
                    f"检测到禁止的 SQL 结构：{node.key.upper()}"
                )

        formatted = statement.sql(dialect="tsql", pretty=True)
        upper_sql = formatted.upper()
        for name in PROHIBITED_SQL_NAMES:
            if name in upper_sql:
                raise SqlWorkbenchValidationError(f"禁止使用 {name}")

        tables = sorted(
            {
                table.sql(dialect="tsql")
                for table in statement.find_all(exp.Table)
                if table.name
            }
        )
        columns = sorted(
            {
                column.sql(dialect="tsql")
                for column in statement.find_all(exp.Column)
                if column.name
            }
        )
        return ValidatedSql(formatted, tables, columns)

    def execute(self, sql: str, max_rows: int) -> QueryResult:
        validated = self.validate(sql)
        limit = max(1, min(max_rows, self.settings.sql_workbench_max_rows))
        started = perf_counter()
        with self.client.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(validated.formatted_sql)
            if cursor.description is None:
                raise SqlWorkbenchValidationError("查询没有返回结果集")
            columns = _unique_columns(cursor.description)
            rows = cursor.fetchmany(limit + 1)
        truncated = len(rows) > limit
        rows = rows[:limit]
        items = [
            {column: _json_value(value) for column, value in zip(columns, row, strict=True)}
            for row in rows
        ]
        return QueryResult(
            columns=columns,
            items=items,
            returned=len(items),
            truncated=truncated,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )

    def execute_excel(
        self,
        source: ExcelTaskDataSource,
        sql: str,
        max_rows: int,
    ) -> QueryResult:
        validated = self.validate(sql)
        limit = max(1, min(max_rows, self.settings.sql_workbench_max_rows))
        try:
            sqlite_sql = transpile(
                validated.formatted_sql,
                read="tsql",
                write="sqlite",
            )[0]
        except Exception as exc:
            raise SqlWorkbenchValidationError(
                "该 T-SQL 语法暂不能在本地 TASK.xlsx 模式执行"
            ) from exc
        started = perf_counter()
        try:
            columns, rows = source.execute_select_with_columns(
                sqlite_sql,
                (),
                limit + 1,
            )
        except Exception as exc:
            raise SqlWorkbenchValidationError(
                f"本地查询执行失败：{exc}"
            ) from exc
        truncated = len(rows) > limit
        items = [
            {key: _json_value(value) for key, value in row.items()}
            for row in rows[:limit]
        ]
        return QueryResult(
            columns=columns,
            items=items,
            returned=len(items),
            truncated=truncated,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )
