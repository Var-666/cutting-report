"""Atomic monitor configuration, result fencing and incident transitions."""
from time import time
from uuid import uuid4

from psycopg.errors import UniqueViolation

from app.database.postgres import PostgresRepository
from app.models.database_monitor import DatabaseMonitorInput


class MonitorNotFound(ValueError):
    pass


class DuplicateMonitor(ValueError):
    pass


class DatabaseMonitorRepository(PostgresRepository):
    def list(self) -> list[dict]:
        with self.connect() as connection:
            return connection.execute('SELECT * FROM database_monitors WHERE deleted_at IS NULL ORDER BY created_at, monitor_id').fetchall()

    @staticmethod
    def _end_incident(connection, row: dict, reason: str, now: float) -> None:
        if row['open_incident_id']:
            connection.execute('UPDATE database_monitor_incidents SET ended_at=%s, end_reason=%s WHERE incident_id=%s AND ended_at IS NULL',
                               (now, reason, row['open_incident_id']))

    def save(self, item: DatabaseMonitorInput, monitor_id: str | None = None) -> str:
        now = time()
        try:
            with self.connect() as connection:
                if monitor_id is None:
                    monitor_id = uuid4().hex
                    connection.execute('''INSERT INTO database_monitors(monitor_id,name,ip,port,enabled,revision,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                        (monitor_id, item.name, str(item.ip), item.port, item.enabled, uuid4().hex, now))
                else:
                    row = connection.execute('SELECT * FROM database_monitors WHERE monitor_id=%s AND deleted_at IS NULL FOR UPDATE', (monitor_id,)).fetchone()
                    if row is None:
                        raise MonitorNotFound('监控目标不存在')
                    reset = (row['ip'], row['port'], row['enabled']) != (str(item.ip), item.port, item.enabled)
                    if reset:
                        self._end_incident(connection, row, 'changed' if item.enabled else 'paused', now)
                        connection.execute('''UPDATE database_monitors SET revision=%s,last_checked_at=NULL,last_success_at=NULL,
                            failure_started_at=NULL,failures=0,successes=0,last_error=NULL,open_incident_id=NULL WHERE monitor_id=%s''',
                            (uuid4().hex, monitor_id))
                    connection.execute('UPDATE database_monitors SET name=%s,ip=%s,port=%s,enabled=%s WHERE monitor_id=%s',
                                       (item.name, str(item.ip), item.port, item.enabled, monitor_id))
        except UniqueViolation:
            raise DuplicateMonitor('该 IP 和端口已添加监控') from None
        return monitor_id

    def delete(self, monitor_id: str) -> None:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM database_monitors WHERE monitor_id=%s AND deleted_at IS NULL FOR UPDATE', (monitor_id,)).fetchone()
            if row is None:
                raise MonitorNotFound('监控目标不存在')
            now = time()
            self._end_incident(connection, row, 'deleted', now)
            connection.execute('UPDATE database_monitors SET deleted_at=%s,enabled=FALSE,revision=%s WHERE monitor_id=%s',
                               (now, uuid4().hex, monitor_id))

    def record(self, expected: dict, error: str | None, checked_at: float) -> bool:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM database_monitors WHERE monitor_id=%s FOR UPDATE', (expected['monitor_id'],)).fetchone()
            if (row is None or row['deleted_at'] is not None or not row['enabled'] or row['revision'] != expected['revision']
                or row['last_checked_at'] != expected['last_checked_at']):
                return False
            # A gap in monitoring cannot count as consecutive successful or failed probes.
            consecutive = row['last_checked_at'] is not None and checked_at - row['last_checked_at'] <= 180
            failures = (row['failures'] if consecutive else 0) + 1 if error else 0
            successes = 0 if error else (row['successes'] if consecutive else 0) + 1
            started = (row['failure_started_at'] if consecutive or row['open_incident_id'] else None) if error else None
            started = (started or checked_at) if error else None
            incident = row['open_incident_id']
            if error and failures >= 3 and not incident:
                incident = uuid4().hex
                connection.execute('''INSERT INTO database_monitor_incidents
                    (incident_id,monitor_id,name,ip,port,started_at,confirmed_at,last_error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (incident, row['monitor_id'], row['name'], row['ip'], row['port'], started, checked_at, error))
            if incident and error:
                connection.execute('UPDATE database_monitor_incidents SET last_error=%s WHERE incident_id=%s', (error, incident))
            if incident and successes >= 2:
                self._end_incident(connection, row, 'recovered', checked_at)
                incident = None
            connection.execute('''UPDATE database_monitors SET last_checked_at=%s,last_success_at=%s,failure_started_at=%s,
                failures=%s,successes=%s,last_error=%s,open_incident_id=%s WHERE monitor_id=%s''',
                (checked_at, checked_at if error is None else row['last_success_at'],
                 started if not incident else (row['failure_started_at'] or started), failures, successes, error, incident, row['monitor_id']))
            return True

    def incidents(self, page: int, page_size: int) -> dict:
        with self.connect(repeatable_read=True) as connection:
            total = connection.execute('SELECT COUNT(*) AS n FROM database_monitor_incidents').fetchone()['n']
            rows = connection.execute('SELECT * FROM database_monitor_incidents ORDER BY started_at DESC,incident_id LIMIT %s OFFSET %s',
                                      (page_size, (page - 1) * page_size)).fetchall()
            return dict(items=rows, total=total, page=page, page_size=page_size)
