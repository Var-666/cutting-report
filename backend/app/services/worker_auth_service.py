from __future__ import annotations

from typing import Any

from app.config import Settings
from app.database.sqlserver import SqlServerClient
from app.database.workshop import AuthenticationError


class WorkerAuthenticationUnavailableError(RuntimeError):
    pass


def _state_text(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)) and int(value) == value:
        return str(int(value))
    return str(value).strip().casefold() if value is not None else ""


class WorkerAuthService:
    """Authenticate workers against the existing HR SQL Server table.

    The plaintext password is compared inside SQL Server and is deliberately
    never selected back into the application process.
    """

    SQL = """
        SELECT
            [_Identify] AS external_id,
            [姓名] AS display_name,
            [集团工号] AS group_no,
            [公司工号] AS company_no,
            [可用] AS available_value,
            CASE WHEN [密码] = ? THEN 1 ELSE 0 END AS password_matches
        FROM [dbo].[HRbase_userInfo]
        WHERE [公司工号] = ?
    """

    def __init__(self, settings: Settings, client: SqlServerClient | None = None) -> None:
        self.settings = settings
        self.client = client or SqlServerClient(settings, source="worker")

    def authenticate(self, company_no: str, password: str) -> dict[str, str]:
        if not self.settings.worker_database_auth_enabled:
            raise WorkerAuthenticationUnavailableError("工人数据库登录尚未启用")

        normalized_company_no = company_no.strip()
        try:
            rows = self.client.fetch_all(
                self.SQL,
                (password, normalized_company_no),
            )
        except Exception as exc:
            raise WorkerAuthenticationUnavailableError(
                "工人登录数据库暂时不可用"
            ) from exc

        # 公司工号必须唯一。重复账号和密码错误都使用相同的对外提示，
        # 避免泄露账号是否存在。
        if len(rows) != 1:
            raise AuthenticationError("公司工号或密码错误")

        row = rows[0]
        password_matches = _state_text(row.get("password_matches")) == "1"
        available = _state_text(row.get("available_value")) == _state_text(
            self.settings.worker_database_available_value
        )
        # HR 的 _Locked 不参与本系统登录；仍校验密码和账号可用状态。
        if not password_matches or not available:
            raise AuthenticationError("公司工号或密码错误")

        external_id = str(row.get("external_id") or "").strip()
        display_name = str(row.get("display_name") or "").strip()
        returned_company_no = str(row.get("company_no") or "").strip()
        if not external_id or not display_name or not returned_company_no:
            raise WorkerAuthenticationUnavailableError("工人账号数据不完整")

        return {
            "external_id": external_id,
            "company_no": returned_company_no,
            "group_no": str(row.get("group_no") or "").strip(),
            "display_name": display_name,
        }
