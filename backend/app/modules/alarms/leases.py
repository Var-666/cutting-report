from datetime import datetime

from psycopg import Connection


class AlarmLeaseLostError(RuntimeError):
    pass


def require_alarm_lease(connection: Connection, owner: str, now: datetime, source_name: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM alarm_sync_state WHERE source_name = %s AND lease_owner = %s AND lease_until > %s FOR UPDATE",
        (source_name, owner, now.isoformat(timespec="seconds")),
    ).fetchone()
    if row is None:
        raise AlarmLeaseLostError("告警执行租约已失效，拒绝提交结果")
