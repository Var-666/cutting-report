from __future__ import annotations

from typing import Any
from collections import OrderedDict
from time import monotonic
import threading

from app.core.report_deadline import ReportOperationTimeout
from app.core.sql_capacity import SqlCapacityError
from app.config import Settings
from app.database.sqlserver import SqlServerClient
from app.database.hr import HrRepository
from app.database.workshop import AuthenticationError
from app.core.request_cancellation import SqlRequestCancelled


class WorkerAuthenticationUnavailableError(RuntimeError):
    pass


def _state_text(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)) and int(value) == value:
        return str(int(value))
    return str(value).strip().casefold() if value is not None else ""


class WorkerAuthService:
    """Authenticate people against the existing HR SQL Server table.

    The plaintext password is compared inside SQL Server and is deliberately
    never selected back into the application process.
    """

    def __init__(self, settings: Settings, client: SqlServerClient | None = None, *, repository: HrRepository | None = None) -> None:
        self.settings = settings
        self.repository = repository or HrRepository(client)
        self._session_checks: OrderedDict[str, tuple[float, bool | None]] = OrderedDict()
        self._session_cache_lock = threading.Lock()
        self._session_query_locks = [threading.Lock() for _ in range(16)]

    def _ensure_enabled(self) -> None:
        if not self.settings.worker_database_auth_enabled:
            raise WorkerAuthenticationUnavailableError("HR人员数据库登录尚未启用")

    def _identity_from_row(self, row: dict[str, Any]) -> dict[str, str] | None:
        available = _state_text(row.get("available_value")) == _state_text(
            self.settings.worker_database_available_value
        )
        external_id = str(row.get("external_id") or "").strip()
        display_name = str(row.get("display_name") or "").strip()
        company_no = str(row.get("company_no") or "").strip()
        group_no = str(row.get("group_no") or "").strip()
        if not available or not external_id or not display_name or not company_no or not group_no:
            return None
        return {
            "external_id": external_id,
            "company_no": company_no,
            "group_no": group_no,
            "display_name": display_name,
        }

    def authenticate(self, group_no: str, password: str) -> dict[str, str]:
        self._ensure_enabled()

        normalized_group_no = group_no.strip()
        try:
            rows = self.repository.authentication_rows(normalized_group_no, password)
        except (SqlRequestCancelled, SqlCapacityError, ReportOperationTimeout):
            raise
        except Exception as exc:
            raise WorkerAuthenticationUnavailableError(
                "HR人员数据库暂时不可用"
            ) from exc

        # 集团工号必须唯一。重复账号和密码错误都使用相同的对外提示，
        # 避免泄露账号是否存在。
        if len(rows) != 1:
            raise AuthenticationError("集团工号或密码错误")

        row = rows[0]
        password_matches = _state_text(row.get("password_matches")) == "1"
        identity = self._identity_from_row(row)
        # HR 的 _Locked 不参与本系统登录；仍校验密码和账号可用状态。
        if not password_matches or identity is None:
            raise AuthenticationError("集团工号或密码错误")
        self._cache_session_check(identity["external_id"], True, self.settings.worker_database_session_check_seconds)
        return identity

    def cached_session_identity_available(self, external_id: str, *, force: bool = False) -> bool | None:
        """Return a cached decision (including outage); None requires an HR query."""
        self._ensure_enabled()
        key = external_id.strip()
        if not key:
            return False
        with self._session_cache_lock:
            cached = self._session_checks.get(key)
            if cached and monotonic() < cached[0] and (not force or cached[1] is None):
                self._session_checks.move_to_end(key)
                if cached[1] is None:
                    raise WorkerAuthenticationUnavailableError("HR人员数据库暂时不可用")
                return cached[1]
        return None

    def session_identity_available(self, external_id: str, *, force: bool = False) -> bool:
        self._ensure_enabled()
        key = external_id.strip()
        if not key:
            return False
        with self._session_query_locks[hash(key) % len(self._session_query_locks)]:
            cached = self.cached_session_identity_available(key, force=force)
            if cached is not None:
                return cached
            try:
                available = self.find_person(key) is not None
            except WorkerAuthenticationUnavailableError:
                self._cache_session_check(key, None, 15)
                raise
            self._cache_session_check(key, available, self.settings.worker_database_session_check_seconds)
            return available

    def _cache_session_check(self, key: str, available: bool | None, ttl: float) -> None:
        with self._session_cache_lock:
            self._session_checks[key] = (monotonic() + ttl, available)
            self._session_checks.move_to_end(key)
            while len(self._session_checks) > 256:
                self._session_checks.popitem(last=False)

    def search_people(self, query: str) -> list[dict[str, str]]:
        self._ensure_enabled()
        normalized = query.strip()
        if len(normalized) < 1:
            return []
        try:
            rows = self.repository.search_people(normalized, self.settings.worker_database_available_value)
        except (SqlRequestCancelled, SqlCapacityError, ReportOperationTimeout):
            raise
        except Exception as exc:
            raise WorkerAuthenticationUnavailableError(
                "HR人员数据库暂时不可用"
            ) from exc
        return [identity for row in rows if (identity := self._identity_from_row(row))]

    def find_person(self, external_id: str) -> dict[str, str] | None:
        self._ensure_enabled()
        try:
            rows = self.repository.person_rows(external_id.strip())
        except (SqlRequestCancelled, SqlCapacityError, ReportOperationTimeout):
            raise
        except Exception as exc:
            raise WorkerAuthenticationUnavailableError(
                "HR人员数据库暂时不可用"
            ) from exc
        if len(rows) != 1:
            return None
        return self._identity_from_row(rows[0])
