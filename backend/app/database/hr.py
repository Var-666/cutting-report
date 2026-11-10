"""Read-only HR queries; authentication decisions stay in WorkerAuthService."""
from typing import Any

from app.core.sql_capacity import sql_identity
from app.database.sqlserver import SqlServerClient


class HrRepository:
    SQL = """
        SELECT
            [_Identify] AS external_id,
            [姓名] AS display_name,
            [集团工号] AS group_no,
            [公司工号] AS company_no,
            [可用] AS available_value,
            CASE WHEN [密码] = ? THEN 1 ELSE 0 END AS password_matches
        FROM [dbo].[HRbase_userInfo]
        WHERE [集团工号] = ?
    """

    DIRECTORY_SEARCH_SQL = """
        SELECT TOP 20
            [_Identify] AS external_id,
            [姓名] AS display_name,
            [集团工号] AS group_no,
            [公司工号] AS company_no,
            [可用] AS available_value
        FROM [dbo].[HRbase_userInfo]
        WHERE [可用] = ?
          AND (
              CONVERT(nvarchar(128), [集团工号]) LIKE ?
              OR CONVERT(nvarchar(128), [公司工号]) LIKE ?
              OR [姓名] LIKE ?
          )
        ORDER BY [姓名], [公司工号]
    """

    PERSON_SQL = """
        SELECT
            [_Identify] AS external_id,
            [姓名] AS display_name,
            [集团工号] AS group_no,
            [公司工号] AS company_no,
            [可用] AS available_value
        FROM [dbo].[HRbase_userInfo]
        WHERE [_Identify] = ?
    """

    def __init__(self, client: SqlServerClient | None) -> None:
        self.client = client

    def _fetch_all(self, sql: str, parameters: tuple) -> list[dict[str, Any]]:
        with sql_identity(None):
            return self.client.fetch_all(sql, parameters)

    def authentication_rows(self, group_no: str, password: str) -> list[dict[str, Any]]:
        return self._fetch_all(self.SQL, (password, group_no))

    def search_people(self, query: str, available_value: Any) -> list[dict[str, Any]]:
        escaped = query.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
        pattern = f"%{escaped}%"
        return self._fetch_all(self.DIRECTORY_SEARCH_SQL, (available_value, pattern, pattern, pattern))

    def person_rows(self, external_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(self.PERSON_SQL, (external_id,))
