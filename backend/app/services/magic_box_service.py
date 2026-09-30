from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import ipaddress
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError


CONFIG_KEYS = (
    "CUTTER_GATEWAY",
    "CUTTER_TOKEN",
    "CUTTER_SECRET",
    "CUTTER01_IP",
    "CUTTER02_IP",
)


class MagicBoxError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        remote_status: int | str | None = None,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.remote_status = remote_status
        self.outcome_unknown = outcome_unknown


class MagicBoxConfigurationError(MagicBoxError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503)


@dataclass(frozen=True, slots=True)
class MagicBoxConfig:
    gateway: str
    token: str = field(repr=False)
    secret: str = field(repr=False)
    machine_ips: tuple[str, ...] = ()


class MagicBoxConfigProvider:
    """Read only the explicit magic-box keys from production SYS_CONFIG."""

    def __init__(self, settings, *, client=None, clock=None) -> None:
        self.settings = settings
        self.client = client or SqlServerClient(settings, source="production")
        self.clock = clock or time.monotonic
        self._cached: MagicBoxConfig | None = None
        self._cached_at = 0.0
        self._lock = threading.RLock()

    @staticmethod
    def _validated(rows: list[dict[str, Any]]) -> MagicBoxConfig:
        values: dict[str, str] = {}
        duplicates: set[str] = set()
        for row in rows:
            key = str(row.get("CONFIG_NAME") or row.get("config_name") or "").strip().upper()
            if key not in CONFIG_KEYS:
                continue
            if key in values:
                duplicates.add(key)
            values[key] = str(row.get("CONFIG_VALUE") or row.get("config_value") or "").strip()
        if duplicates:
            raise MagicBoxConfigurationError(
                "SYS_CONFIG存在重复的魔盒配置键：" + "、".join(sorted(duplicates))
            )
        missing = [key for key in CONFIG_KEYS[:3] if not values.get(key)]
        if missing:
            raise MagicBoxConfigurationError(
                "SYS_CONFIG缺少魔盒配置：" + "、".join(missing)
            )
        gateway = values["CUTTER_GATEWAY"].rstrip("/")
        parsed = urlsplit(gateway)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise MagicBoxConfigurationError(
                "CUTTER_GATEWAY应为不含路径、账号或参数的http(s)地址"
            )
        if any(ord(char) < 32 for key in ("CUTTER_TOKEN", "CUTTER_SECRET") for char in values[key]):
            raise MagicBoxConfigurationError("魔盒Token或Secret包含非法控制字符")
        machines = []
        for key in CONFIG_KEYS[3:]:
            value = values.get(key, "")
            if not value:
                continue
            try:
                parsed_ip = ipaddress.ip_address(value)
            except ValueError:
                raise MagicBoxConfigurationError(f"{key}不是有效IP地址") from None
            if parsed_ip.version != 4:
                raise MagicBoxConfigurationError(f"{key}目前只支持IPv4地址")
            if value not in machines:
                machines.append(value)
        return MagicBoxConfig(
            gateway=gateway,
            token=values["CUTTER_TOKEN"],
            secret=values["CUTTER_SECRET"],
            machine_ips=tuple(machines),
        )

    def get(self, *, force: bool = False) -> MagicBoxConfig:
        if not self.settings.magic_box_enabled:
            raise MagicBoxConfigurationError("尚未启用魔盒接口，请设置MAGIC_BOX_ENABLED=true")
        with self._lock:
            now = self.clock()
            ttl = max(0, int(self.settings.magic_box_config_cache_seconds))
            if not force and self._cached and now - self._cached_at < ttl:
                return self._cached
            try:
                rows = self.client.fetch_all(
                    """
                    SELECT CONFIG_NAME, CONFIG_VALUE
                    FROM dbo.SYS_CONFIG
                    WHERE CONFIG_NAME IN (?, ?, ?, ?, ?)
                    """,
                    CONFIG_KEYS,
                )
            except SqlServerConfigurationError as exc:
                raise MagicBoxConfigurationError(str(exc)) from None
            except Exception:
                raise MagicBoxConfigurationError(
                    "读取SYS_CONFIG失败，请检查生产数据库连接和只读权限"
                ) from None
            self._cached = self._validated(rows)
            self._cached_at = now
            return self._cached

    def public_status(self) -> dict[str, Any]:
        try:
            config = self.get()
            return {
                "configured": True,
                "gateway": config.gateway,
                "machine_ips": list(config.machine_ips),
                "message": "已从SYS_CONFIG读取魔盒连接配置；Token和Secret不会返回前端。",
            }
        except MagicBoxError as exc:
            return {
                "configured": False,
                "gateway": None,
                "machine_ips": [],
                "message": str(exc),
            }


class MagicBoxClient:
    """Small FSCUT magic-box client with monotonic per-request signatures."""

    def __init__(
        self,
        config: MagicBoxConfig,
        *,
        timeout: float = 60,
        timezone: str = "Asia/Shanghai",
        transport=None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self.timezone = timezone
        self.transport = transport
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._time_offset_ms = 0
        self._last_timestamp = 0
        self._timestamp_lock = threading.Lock()

    def _timestamp(self) -> str:
        with self._timestamp_lock:
            candidate = int(self.clock_ms()) + self._time_offset_ms
            candidate = max(candidate, self._last_timestamp + 1)
            self._last_timestamp = candidate
            return str(candidate)

    def _headers(self) -> dict[str, str]:
        timestamp = self._timestamp()
        source = (
            f"timestamp={timestamp}&token={self.config.token}"
            f"&secret={self.config.secret}"
        )
        sign = hashlib.md5(source.encode("utf-8"), usedforsecurity=False).hexdigest()
        return {"token": self.config.token, "timestamp": timestamp, "sign": sign}

    def _client(self) -> httpx.Client:
        if not 1 <= float(self.timeout) <= 300:
            raise MagicBoxConfigurationError("魔盒请求超时配置必须在1至300秒之间")
        return httpx.Client(
            base_url=self.config.gateway,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        upload: bool = False,
        effect: bool = False,
        **kwargs,
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if authenticated:
            headers.update(self._headers())
        try:
            with self._client() as client:
                response = client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException:
            raise MagicBoxError(
                "魔盒请求超时；上传结果无法确认时不要盲目重复上传",
                outcome_unknown=upload or effect,
            ) from None
        except httpx.HTTPError:
            raise MagicBoxError("魔盒连接失败，请检查内网地址、端口和网络", outcome_unknown=upload or effect) from None
        try:
            payload = response.json()
        except ValueError:
            raise MagicBoxError(
                f"魔盒返回HTTP {response.status_code}，但响应不是JSON",
                outcome_unknown=upload or effect,
            ) from None
        if not isinstance(payload, dict):
            raise MagicBoxError("魔盒响应格式不正确", outcome_unknown=upload or effect)
        remote_status = payload.get("status")
        if response.status_code != 200 or type(remote_status) is not int or remote_status != 0:
            message = str(payload.get("msg") or payload.get("error") or "请求失败").strip()
            if len(message) > 180:
                message = message[:180]
            raise MagicBoxError(
                f"魔盒返回失败：{message or '未知错误'}",
                remote_status=remote_status,
                # Only a valid business rejection proves that the operation did
                # not take effect. Transport/server/protocol failures do not.
                outcome_unknown=(upload or effect) and not (
                    response.status_code == 200
                    and type(remote_status) is int and remote_status != 0
                ),
            )
        return payload.get("data")

    def sync_time(self) -> dict[str, Any]:
        raw = self._request("GET", "/api/time", authenticated=False)
        try:
            box_time = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise MagicBoxError("魔盒时间接口返回格式不正确") from None
        local_time = datetime.now(ZoneInfo(self.timezone)).replace(tzinfo=None)
        delta = int((box_time - local_time).total_seconds() * 1000)
        self._time_offset_ms = delta
        return {
            "box_time": box_time.isoformat(timespec="seconds"),
            "offset_seconds": round(delta / 1000, 3),
        }

    def list_machines(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/datacenter/list")
        if data is None:
            return []
        if not isinstance(data, list):
            raise MagicBoxError("魔盒机床列表格式不正确")
        output = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            ip = str(raw.get("serverIP") or "").strip()
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue
            output.append(
                {
                    "machine_ip": ip,
                    "server_name": str(raw.get("serverName") or "").strip() or None,
                    "alive_time": raw.get("aliveTime"),
                    "applications": [
                        {
                            "name": str(item.get("appName") or "").strip(),
                            "version": str(item.get("appVer") or "").strip(),
                        }
                        for item in (raw.get("appInfos") or raw.get("apps") or [])
                        if isinstance(item, dict)
                    ],
                }
            )
        return output

    def upload_task(
        self,
        *,
        task_identifier: str,
        task_name: str,
        file_path: Path,
        material: str | None,
        thickness: float | None,
        count: int,
    ) -> str:
        data = {
            "taskIdentifier": task_identifier,
            "taskName": task_name,
            "material": material or "",
            "thickness": str(thickness if thickness is not None else 0),
            "count": str(count),
        }
        request_started = False
        try:
            with file_path.open("rb") as stream:
                request_started = True
                remote_id = self._request(
                    "POST",
                    "/api/task/upload",
                    data=data,
                    files={
                        "taskFile": (
                            file_path.name,
                            stream,
                            "application/octet-stream",
                        )
                    },
                    upload=True,
                )
        except OSError:
            raise MagicBoxError("本地切割文件无法读取", status_code=409, outcome_unknown=request_started) from None
        if not isinstance(remote_id, str) or not remote_id.strip() or len(remote_id.strip()) > 128:
            raise MagicBoxError("魔盒上传成功但未返回有效任务UUID", outcome_unknown=True)
        remote_id = remote_id.strip()
        return remote_id

    def assign_task(self, *, task_identifier: str, machine_ip: str) -> None:
        self._request(
            "POST",
            "/api/task/assign",
            effect=True,
            json={"taskIdentifier": task_identifier, "machineIp": machine_ip},
        )

    def cancel_assignment(self, *, task_identifier: str) -> None:
        self._request(
            "POST",
            "/api/task/cancelAssign",
            effect=True,
            json={"taskIdentifier": task_identifier},
        )
