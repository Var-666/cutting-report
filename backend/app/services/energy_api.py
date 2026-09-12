"""Read-only client for the supplied EnergyPEC API contract.

urllib deliberately avoids httpx's INFO request-URL logging: Login puts the
password in its query string. Credentials/tokens never appear in exceptions,
diagnostics or the reading cache, and redirects are not followed.
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta
from http.client import HTTPException as HttpClientError
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from app.config import Settings


class EnergyApiError(RuntimeError):
    pass


class EnergyAuthenticationError(EnergyApiError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def decode_json(value):
    # The document shows escaped JSON; deployments may return a JSON string.
    for _ in range(3):
        if not isinstance(value, (str, bytes)):
            return value
        try:
            value = json.loads(value)
        except (ValueError, UnicodeError):
            raise EnergyApiError("能管接口返回的不是有效 JSON") from None
    if isinstance(value, (str, bytes)):
        raise EnergyApiError("能管接口返回了无法解析的数据")
    return value


class EnergyApiClient:
    def __init__(self, settings: Settings, *, opener=None):
        self.settings = settings
        self.opener = opener or build_opener(ProxyHandler({}), _NoRedirect())
        self._lock = threading.RLock()
        self._auth_lock = threading.Lock()
        self._query_locks = [threading.Lock() for _ in range(32)]
        self._company_id = ""
        self._auth_id = ""
        self._auth_until = 0.0
        self._cache: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
        self._failure: tuple[float, str] | None = None

    def _request(self, path: str, *, params=None, body=None, auth=False):
        base = self.settings.energy_api_base_url.strip().rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise EnergyApiError("能管接口地址配置无效")
        url = base + "/" + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Accept": "application/json", "isZipEn": "0"}
        if auth:
            headers["Authorization"] = "BasicAuth " + self._auth_id
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with self.opener.open(request, timeout=max(1, self.settings.energy_api_timeout)) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status in {401, 403}:
                raise EnergyAuthenticationError("能管平台认证失败，请核对账号或权限") from None
            raise EnergyApiError(f"能管接口返回 HTTP {status}") from None
        except (URLError, OSError, ValueError, HttpClientError):
            raise EnergyApiError("能管接口连接失败，请在内网检查地址、端口及服务状态") from None
        # RemoteDisconnected inherits from ConnectionError/OSError.
        if len(raw) > 16 * 1024 * 1024:
            raise EnergyApiError("能管接口单次响应过大，请缩短查询区间")
        data = decode_json(raw)
        if isinstance(data, dict) and data.get("IsSuccess") is False:
            if str(data.get("Content")) in {"401", "403"}:
                raise EnergyAuthenticationError("能管平台认证失败，请核对账号或权限")
            # Do not forward arbitrary server Remark (may contain credentials).
            raise EnergyApiError("能管平台拒绝查询，请核对参数编号及账号权限")
        return data

    def _login(self):
        if not self.settings.energy_api_username or not self.settings.energy_api_password:
            raise EnergyApiError("尚未配置能管账号和密码")
        data = self._request("api/Account/Login", params={
            "email": self.settings.energy_api_username,
            "pwd": self.settings.energy_api_password,
        })
        content = data.get("Content") if isinstance(data, dict) else None
        if not isinstance(data, dict) or data.get("IsSuccess") is not True or not isinstance(content, dict):
            raise EnergyAuthenticationError("能管登录未成功，请核对账号与密码")
        company_id, auth_id = content.get("CompanyId"), content.get("AuthId")
        if not all(isinstance(value, str) and value.strip() and "\r" not in value and "\n" not in value for value in (company_id, auth_id)):
            raise EnergyApiError("能管登录结果缺少 CompanyId 或 AuthId")
        self._company_id, self._auth_id = company_id, auth_id
        self._auth_until = time.monotonic() + 600

    def _query(self, ids, start, end):
        for attempt in range(2):
            if time.monotonic() >= self._auth_until:
                with self._auth_lock:
                    if time.monotonic() >= self._auth_until:
                        self._login()
            try:
                data = self._request("api/CollectValue/GetCollectValueByMfIdAndDate", auth=True, body={
                    "CompanyId": self._company_id,
                    "Ids": list(ids),
                    "Stime": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "Etime": end.strftime("%Y-%m-%d %H:%M:%S"),
                })
                if isinstance(data, dict) and "Content" in data:
                    data = decode_json(data["Content"])
                if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                    raise EnergyApiError("能管采集值响应结构不符合接口说明")
                return data
            except EnergyAuthenticationError:
                with self._auth_lock:
                    self._auth_id = ""
                    self._auth_until = 0
                if attempt:
                    raise

    def fetch(self, ids: tuple[str, ...], start: datetime, end: datetime, *, force=False) -> list[dict]:
        if not ids or end <= start or end - start > timedelta(days=367):
            raise EnergyApiError("能管查询参数或时间区间无效（最多 366 天）")
        with self._lock:
            if self._failure and not force and time.monotonic() < self._failure[0]:
                raise EnergyApiError(self._failure[1])
        collected = []
        cursor = start.replace(microsecond=0)
        # Include fractional end seconds rather than rounding the boundary down.
        end = end.replace(microsecond=0) + (timedelta(seconds=1) if end.microsecond else timedelta())
        while cursor < end:
            stop = min(cursor + timedelta(hours=24), end)
            key = (tuple(sorted(ids)), cursor, stop)
            query_lock = self._query_locks[hash(key) % len(self._query_locks)]
            with query_lock:
                with self._lock:
                    cached = self._cache.get(key)
                    if cached and not force and time.monotonic() < cached[0]:
                        rows = cached[1]
                        self._cache.move_to_end(key)
                    else:
                        rows = None
                if rows is None:
                    try:
                        rows = self._query(ids, cursor, stop)
                    except EnergyApiError as exc:
                        with self._lock:
                            self._cache.pop(key, None)
                            self._failure = (time.monotonic() + 15, str(exc))
                        raise
                    with self._lock:
                        self._failure = None
                        self._cache[key] = (
                            time.monotonic() + max(0, self.settings.energy_cache_seconds),
                            rows,
                        )
                        self._cache.move_to_end(key)
                        while len(self._cache) > 128:
                            self._cache.popitem(last=False)
            collected.extend(rows)
            cursor = stop
        return deepcopy(collected)
