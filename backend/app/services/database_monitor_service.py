"""Bounded TCP probes; no credentials, SQL, model calls or automatic restarts."""
import errno
import socket
from time import time

from app.database.database_monitors import DatabaseMonitorRepository


class DatabaseMonitorService:
    INTERVAL = 60
    STALE_AFTER = 180
    TIMEOUT = 5

    def __init__(self, repository: DatabaseMonitorRepository, timezone: str):
        self.repository = repository
        self.timezone = timezone

    def list(self) -> list[dict]:
        now = time()
        items = self.repository.list()
        for item in items:
            if not item['enabled']:
                state = 'paused'
            elif item['last_checked_at'] is None or now - item['last_checked_at'] > self.STALE_AFTER:
                state = 'unknown'
            elif item['open_incident_id']:
                state = 'recovering' if item['successes'] else 'down'
            elif item['failures']:
                state = 'suspect'
            else:
                state = 'healthy'
            item['state'] = state
        return items

    @staticmethod
    def probe(ip: str, port: int) -> str | None:
        try:
            with socket.create_connection((ip, port), timeout=DatabaseMonitorService.TIMEOUT):
                return None
        except TimeoutError:
            return '连接超时'
        except ConnectionRefusedError:
            return '端口拒绝连接'
        except OSError as exc:
            return '网络或主机不可达' if exc.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH} else '连接失败'

    def run_once(self, requested_at: float) -> None:
        for item in self.repository.list():
            if not item['enabled'] or (item['last_checked_at'] is not None and item['last_checked_at'] >= requested_at):
                continue
            error = self.probe(item['ip'], item['port'])
            self.repository.record(item, error, time())
