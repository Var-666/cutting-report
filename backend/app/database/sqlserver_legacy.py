"""One-shot read-only ODBC queries with old TLS isolated from Web/Worker TLS."""
from __future__ import annotations

import os
from pathlib import Path
import pickle
import subprocess
import sys
from time import monotonic
from typing import Any

from app.core.report_deadline import ensure_time_remaining, remaining_seconds, operation_deadline
from app.core.request_cancellation import (
    SqlRequestCancelled, current_request_id, sql_request_cancellations,
)
from app.models.database_sources import DatabaseSourceConfig


def fetch_all(config: DatabaseSourceConfig, sql: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
    ensure_time_remaining()
    request_id = current_request_id.get()
    if sql_request_cancellations.is_cancelled(request_id):
        raise SqlRequestCancelled('SQL 查询已取消')
    # Credentials travel only through the child's stdin, never argv, environment or disk.
    payload = pickle.dumps((config.model_copy(update={'legacy_tls': False}), sql, parameters))
    env = {**os.environ, 'OPENSSL_CONF': str(Path(__file__).with_suffix('.cnf'))}
    timeout = config.connect_timeout + config.query_timeout + 1
    started = monotonic()
    process = subprocess.Popen([sys.executable, '-m', 'app.database.sqlserver_legacy'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    # Popen.kill also implements the cancellation registry's cursor.cancel contract.
    class PendingQuery:
        def cancel(self) -> None:
            if process.poll() is None:
                process.kill()
    pending = PendingQuery()
    cancelled = sql_request_cancellations.register(request_id, pending)
    try:
        if cancelled:
            raise SqlRequestCancelled('SQL 查询已取消')
        first_write = payload
        while True:
            if sql_request_cancellations.is_cancelled(request_id):
                raise SqlRequestCancelled('SQL 查询已取消')
            ensure_time_remaining()
            budget = timeout - (monotonic() - started)
            remaining = remaining_seconds()
            if remaining is not None:
                budget = min(budget, remaining)
            if budget <= 0:
                ensure_time_remaining()
                raise TimeoutError('旧版 SQL Server 连接或查询超时')
            try:
                output, _ = process.communicate(input=first_write, timeout=min(0.1, budget))
                break
            except subprocess.TimeoutExpired:
                first_write = None
        if sql_request_cancellations.is_cancelled(request_id):
            raise SqlRequestCancelled('SQL 查询已取消')
        ensure_time_remaining()
        if process.returncode != 0 or not output:
            raise RuntimeError('旧版 SQL Server 隔离查询进程失败')
        # Both ends are this trusted module, connected by private anonymous pipes.
        success, result = pickle.loads(output)
        if not success:
            raise RuntimeError(f'旧版 SQL Server 只读查询失败（{result}）')
        return result
    finally:
        sql_request_cancellations.unregister(request_id, pending)
        if process.poll() is None:
            process.kill()
        process.communicate()


def main() -> None:
    from app.database.sqlserver import SqlServerClient
    try:
        config, sql, parameters = pickle.load(sys.stdin.buffer)
        with operation_deadline(config.connect_timeout + config.query_timeout):
            result = SqlServerClient(config).fetch_all(sql, parameters)
        response = (True, result)
    except Exception as exc:
        # ODBC exception text can contain credentials; do not forward it across the boundary.
        response = (False, type(exc).__name__)
    pickle.dump(response, sys.stdout.buffer, protocol=pickle.HIGHEST_PROTOCOL)


if __name__ == '__main__':
    main()
