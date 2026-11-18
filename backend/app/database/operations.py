"""Operations persistence and read-only projections of existing business state."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.database.postgres import PostgresRepository, lock_key

INCIDENT_COLUMNS = ('error_code', 'level', 'component', 'role', 'instance_id',
                    'line_id', 'source_id', 'resource_id', 'message')


def fingerprint(item):
    if item.get('error_code') == 'POSTGRES_UNAVAILABLE':
        return sha256(b'POSTGRES_UNAVAILABLE').hexdigest()
    identity = [item.get(key) for key in ('error_code', 'component', 'role', 'line_id', 'source_id', 'resource_id')]
    return sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()


class OperationsRepository(PostgresRepository):
    def connect(self, *, repeatable_read: bool = False):
        # A diagnostic query should leave the shared pool queue quickly under load.
        return (super().connect(repeatable_read=repeatable_read) if self._connection is not None else
                self.database.connect(repeatable_read=repeatable_read, checkout_timeout=0.25))

    @staticmethod
    def _limits(connection):
        connection.execute("SET LOCAL statement_timeout = '1500ms'")
        connection.execute("SET LOCAL transaction_timeout = '3500ms'")
        connection.execute("SET LOCAL lock_timeout = '300ms'")

    @staticmethod
    def _record(connection, item, *, observed=False):
        context = {key: item[key] for key in ('request_id', 'analysis_id', 'task_id', 'report_id', 'stage', 'related_kind')
                   if item.get(key)}
        timestamp = item['timestamp']
        source_count = item.get('source_count')
        identity = fingerprint(item)
        lock_key(connection, 'operations:' + identity)
        connection.execute("""
            INSERT INTO system_incidents (incident_id,fingerprint,error_code,level,component,role,instance_id,
              line_id,source_id,resource_id,message,context,first_seen_at,last_seen_at,occurrences)
            SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            WHERE NOT EXISTS (SELECT 1 FROM system_incidents WHERE fingerprint=%s AND recovered_at >= %s)
            ON CONFLICT(fingerprint) WHERE recovered_at IS NULL DO UPDATE SET
                first_seen_at=LEAST(system_incidents.first_seen_at,excluded.first_seen_at),
                last_seen_at=GREATEST(system_incidents.last_seen_at,excluded.last_seen_at),
                occurrences=CASE WHEN %s THEN GREATEST(system_incidents.occurrences,excluded.occurrences)
                                 ELSE system_incidents.occurrences+1 END,
                instance_id=CASE WHEN excluded.last_seen_at >= system_incidents.last_seen_at THEN excluded.instance_id ELSE system_incidents.instance_id END,
                message=CASE WHEN excluded.last_seen_at >= system_incidents.last_seen_at THEN excluded.message ELSE system_incidents.message END,
                context=CASE WHEN excluded.last_seen_at >= system_incidents.last_seen_at THEN excluded.context ELSE system_incidents.context END
            WHERE NOT %s OR excluded.last_seen_at > system_incidents.last_seen_at
                OR (%s AND excluded.occurrences > system_incidents.occurrences)
        """, (item.get('id') or uuid4().hex, identity, *(item.get(key) for key in INCIDENT_COLUMNS),
              Jsonb(context), item.get('source_first_at') or timestamp, timestamp, source_count or 1,
              identity, timestamp, source_count is not None, observed, source_count is not None))

    def record(self, items):
        with self.connect() as connection:
            self._limits(connection)
            # A consistent order reduces contention across Consumer batches.
            for item in sorted(items, key=lambda item: (fingerprint(item), item['timestamp'])):
                self._record(connection, item)

    @staticmethod
    def _recover(connection, item, evidence):
        identity = fingerprint(item)
        lock_key(connection, 'operations:' + identity)
        connection.execute("""
            UPDATE system_incidents SET recovered_at=%s,recovery_evidence=%s
            WHERE fingerprint=%s AND recovered_at IS NULL AND last_seen_at <= %s
        """, (item['timestamp'], evidence, identity, item['timestamp']))

    def observe(self, observations, business, recoveries, now: datetime):
        bucket = int(now.timestamp()) // 30
        with self.connect() as connection:
            self._limits(connection)
            # Web and Scheduler both observe. Only one committed observer per window counts.
            if not connection.execute("SELECT pg_try_advisory_xact_lock(740314010) AS locked").fetchone()['locked']:
                return
            if not connection.execute("""
                INSERT INTO operations_samples VALUES ('cycle',true,%s,1)
                ON CONFLICT(sample_key) DO UPDATE SET bucket=excluded.bucket
                WHERE operations_samples.bucket<excluded.bucket RETURNING bucket
            """, (bucket,)).fetchone():
                return
            for item, healthy, evidence in observations:
                sample_key = fingerprint(item)
                if healthy is None:
                    connection.execute('DELETE FROM operations_samples WHERE sample_key=%s', (sample_key,))
                    continue
                state = connection.execute("""
                    INSERT INTO operations_samples VALUES (%s,%s,%s,1)
                    ON CONFLICT(sample_key) DO UPDATE SET healthy=excluded.healthy,bucket=excluded.bucket,
                        streak=CASE WHEN operations_samples.healthy=excluded.healthy
                            AND excluded.bucket-operations_samples.bucket<=2
                            THEN LEAST(100, operations_samples.streak+1) ELSE 1 END
                    RETURNING streak
                """, (sample_key, healthy, bucket)).fetchone()
                if state['streak'] >= 2:
                    if healthy:
                        self._recover(connection, item, evidence)
                    elif not item.get('recovery_only'):
                        self._record(connection, item, observed=True)
            for item in business:
                self._record(connection, item, observed=True)
            for row in recoveries:
                if row['recovered_at'] is not None:
                    lock_key(connection, 'operations:' + row['fingerprint'])
                    connection.execute("""
                        UPDATE system_incidents SET recovered_at=%s,recovery_evidence=%s
                        WHERE fingerprint=%s AND recovered_at IS NULL AND last_seen_at <= %s
                    """, (row['recovered_at'], row['evidence'], row['fingerprint'], row['recovered_at']))

    def list(self, filters):
        where = ['last_seen_at <= %s']
        values = [filters.end]
        start = filters.start
        if start is None and filters.status not in ('open', 'acknowledged'):
            start = filters.end - timedelta(days=30)
        if start is not None:
            where.append('last_seen_at >= %s')
            values.append(start)
        for name in ('status', 'level', 'component', 'line_id', 'error_code'):
            value = getattr(filters, name)
            if value is not None:
                where.append(f'{name}=%s')
                values.append(value)
        if filters.keyword:
            where.append('strpos(lower(message),lower(%s))>0')
            values.append(filters.keyword)
        clause = ' AND '.join(where)
        with self.connect(repeatable_read=True) as connection:
            self._limits(connection)
            total = connection.execute(f'SELECT count(*) AS count FROM system_incidents WHERE {clause}', values).fetchone()['count']
            rows = connection.execute(f'SELECT * FROM system_incidents WHERE {clause} ORDER BY last_seen_at DESC,incident_id DESC LIMIT %s OFFSET %s',
                                      (*values, filters.page_size, (filters.page - 1) * filters.page_size)).fetchall()
        return dict(items=rows, total=total, page=filters.page, page_size=filters.page_size)

    def has_pending_incident(self) -> bool:
        with self.connect() as connection:
            self._limits(connection)
            return connection.execute("SELECT EXISTS (SELECT 1 FROM system_incidents WHERE status='open') AS pending").fetchone()['pending']

    def summary(self):
        with self.connect(repeatable_read=True) as connection:
            self._limits(connection)
            count = connection.execute("SELECT count(*) AS n FROM system_incidents WHERE status='open'").fetchone()['n']
            rows = connection.execute('SELECT * FROM system_incidents ORDER BY last_seen_at DESC,incident_id DESC LIMIT 10').fetchall()
            recurring = connection.execute('''SELECT * FROM system_incidents
                WHERE recovered_at IS NULL AND occurrences>1
                ORDER BY last_seen_at DESC,incident_id DESC LIMIT 5''').fetchall()
        return count, rows, recurring

    def get(self, incident_id):
        with self.connect() as connection:
            self._limits(connection)
            return connection.execute('SELECT * FROM system_incidents WHERE incident_id=%s', (incident_id,)).fetchone()

    def acknowledge(self, incident_id, actor, note):
        with self.connect() as connection:
            self._limits(connection)
            return connection.execute("""
                UPDATE system_incidents SET acknowledged_at=clock_timestamp(),acknowledged_by=%s,note=%s
                WHERE incident_id=%s AND status='open' RETURNING *
            """, (actor, note, incident_id)).fetchone()

    def cleanup(self, cutoff, limit):
        with self.connect() as connection:
            self._limits(connection)
            connection.execute("""
                DELETE FROM system_incidents WHERE incident_id IN (
                    SELECT incident_id FROM system_incidents WHERE recovered_at < %s ORDER BY recovered_at LIMIT %s
                )
            """, (cutoff, limit))
            connection.execute("DELETE FROM operations_samples WHERE bucket < (extract(epoch FROM now()) / 30)::bigint - 86400")

    def work_status(self, now, stall_seconds):
        with self.connect(repeatable_read=True) as connection:
            self._limits(connection)
            rows = connection.execute("""
                SELECT 'background' AS "group",status,count(*) AS count FROM
                  (SELECT status FROM background_tasks WHERE status IN ('pending','deferred','running','blocked','failed') LIMIT 10001) b GROUP BY status
                UNION ALL SELECT 'reports',status,count(*) FROM
                  (SELECT status FROM report_jobs WHERE status IN ('pending','querying','calculating','analyzing','rendering','failed')
                      AND (status<>'failed' OR finished_at::timestamptz >= %s::timestamptz) LIMIT 10001) j GROUP BY status
                UNION ALL SELECT 'automation',status,count(*) FROM
                  (SELECT status FROM scheduled_report_runs WHERE status='failed' AND updated_at >= %s LIMIT 10001) a GROUP BY status
                UNION ALL SELECT 'email',status,count(*) FROM
                  (SELECT status FROM email_deliveries WHERE status IN ('failed','unknown','retry_wait','pending','sending')
                      AND (status<>'failed' OR updated_at >= %s) LIMIT 10001) e GROUP BY status
            """, (now, now, now)).fetchall()
            pressure = connection.execute("""
                SELECT
                  EXISTS(SELECT 1 FROM background_tasks WHERE status IN ('pending','deferred') AND next_attempt < extract(epoch FROM now())-%s) AS queue_late,
                  EXISTS(SELECT 1 FROM background_tasks WHERE status='running' AND updated_at < extract(epoch FROM now())-%s) AS task_stalled
            """, (stall_seconds, stall_seconds)).fetchone()
        return rows, pressure

    def business_status(self, since, error_codes: dict[tuple[str, str], str]):
        background_codes = Jsonb({status: code for (kind, status), code in error_codes.items() if kind == 'background'})
        email_codes = Jsonb({status: code for (kind, status), code in error_codes.items() if kind == 'email'})
        with self.connect(repeatable_read=True) as connection:
            self._limits(connection)
            failures = connection.execute("""
                (SELECT 'background' AS kind,b.task_id AS id,b.device_id,NULL AS report_id,b.status,
                   to_timestamp(b.updated_at) AS stamp,b.kind AS stage,NULL AS request_id
                 FROM background_tasks b
                 WHERE (b.status='blocked' OR (
                     (b.status='failed' OR (b.status='deferred' AND b.attempts>=2
                         AND b.last_error NOT IN ('EXECUTOR_ADMISSION','INTERRUPTED')))
                     AND b.updated_at >= extract(epoch FROM %s::timestamptz)
                 )) AND NOT EXISTS (
                     SELECT 1 FROM system_incidents o
                     WHERE o.component='background' AND o.resource_id=b.task_id
                         AND o.error_code=(%s::jsonb->>b.status)
                         AND o.last_seen_at >= to_timestamp(b.updated_at)
                 )
                 ORDER BY b.updated_at DESC,b.task_id LIMIT 50)
                UNION ALL
                (SELECT 'reports',j.report_id,j.device_id,j.report_id,j.status,j.finished_at::timestamptz,NULL,j.request_id
                 FROM report_jobs j WHERE j.status='failed' AND j.finished_at::timestamptz >= %s::timestamptz
                     AND NOT EXISTS (
                         SELECT 1 FROM system_incidents o
                         WHERE o.component='reports' AND o.resource_id=j.report_id
                             AND o.last_seen_at >= j.finished_at::timestamptz
                     )
                 ORDER BY j.finished_at::timestamptz DESC,j.report_id LIMIT 50)
                UNION ALL
                (SELECT 'automation',r.run_id,r.device_id,r.report_id,r.status,r.updated_at::timestamptz,NULL,NULL
                 FROM scheduled_report_runs r WHERE r.status='failed' AND r.updated_at >= %s
                     AND NOT EXISTS (
                         SELECT 1 FROM system_incidents o
                         WHERE o.component='automation' AND o.resource_id=r.run_id
                             AND o.last_seen_at >= r.updated_at::timestamptz
                     )
                 ORDER BY r.updated_at DESC,r.run_id LIMIT 50)
                UNION ALL
                (SELECT 'email',e.delivery_id,r.device_id,e.report_id,e.status,e.updated_at::timestamptz,NULL,NULL
                 FROM email_deliveries e JOIN scheduled_report_runs r USING(run_id)
                 WHERE e.status IN ('failed','unknown','retry_wait') AND (e.updated_at >= %s OR e.status='unknown')
                     AND NOT EXISTS (
                         SELECT 1 FROM system_incidents o
                         WHERE o.component='email' AND o.resource_id=e.delivery_id
                             AND o.error_code=(%s::jsonb->>e.status)
                             AND o.last_seen_at >= e.updated_at::timestamptz
                     )
                 ORDER BY e.updated_at DESC,e.delivery_id LIMIT 50)
            """, (since, background_codes, since, since, since, email_codes)).fetchall()
            failures.extend(connection.execute("""
                SELECT 'database-monitor' AS kind,m.incident_id AS id,m.monitor_id AS device_id,
                  NULL AS report_id,'down' AS status,NULL AS stage,NULL AS request_id,
                  to_timestamp(m.started_at) AS source_first_at,
                  to_timestamp(m.last_failed_at) AS stamp,
                  m.failure_count AS source_count,m.last_error,
                  CASE WHEN m.end_reason='recovered' THEN to_timestamp(m.ended_at) END AS verified_at
                FROM database_monitor_incidents m
                WHERE (m.ended_at IS NULL OR m.ended_at >= extract(epoch FROM %s::timestamptz))
                    AND NOT EXISTS (
                        SELECT 1 FROM system_incidents o
                        WHERE o.component='database-monitor' AND o.resource_id=m.incident_id
                            AND o.last_seen_at >= to_timestamp(m.last_failed_at) AND o.occurrences >= m.failure_count
                    )
                ORDER BY m.last_failed_at DESC,m.incident_id LIMIT 50
            """, (since,)).fetchall())
            recovered = connection.execute("""
                SELECT o.fingerprint, evidence.recovered_at,
                    o.component, resolution.created_at IS NOT NULL AS manually_verified
                FROM system_incidents o
                LEFT JOIN database_monitor_incidents m ON o.component='database-monitor' AND o.resource_id=m.incident_id
                LEFT JOIN email_deliveries e ON o.component='email' AND o.resource_id=e.delivery_id
                LEFT JOIN LATERAL (
                    SELECT r.created_at FROM email_delivery_resolutions r
                    WHERE o.error_code='EMAIL_RESULT_UNKNOWN' AND e.status<>'unknown'
                        AND r.delivery_id=e.delivery_id AND r.message_id=e.message_id
                        AND r.created_at::timestamptz>=o.last_seen_at
                    ORDER BY r.created_at DESC LIMIT 1
                ) resolution ON TRUE
                LEFT JOIN scheduled_report_runs a ON o.component='automation' AND o.resource_id=a.run_id
                LEFT JOIN report_jobs j ON o.component='reports' AND o.resource_id=j.report_id
                LEFT JOIN background_tasks b ON o.component='background' AND o.resource_id=b.task_id
                LEFT JOIN cutting_dispatches d ON o.component='services.cutting_dispatch_service'
                    AND o.error_code IN ('DISPATCH_UPLOAD','DISPATCH_ASSIGN') AND o.resource_id=d.dispatch_id
                LEFT JOIN energy_history_queue h ON o.component='workers.energy_history_worker'
                    AND o.error_code='ENERGY_HISTORY_SYNC_FAILED' AND o.source_id LIKE 'energy:%'
                    AND h.source_key=substr(o.source_id,8) AND h.report_month=o.resource_id
                CROSS JOIN LATERAL (
                    SELECT CASE WHEN m.end_reason='recovered' AND o.last_seen_at >= to_timestamp(m.last_failed_at)
                            AND o.occurrences >= m.failure_count THEN to_timestamp(m.ended_at)
                        WHEN resolution.created_at IS NOT NULL THEN resolution.created_at::timestamptz
                        WHEN e.status='sent' THEN COALESCE(e.sent_at,e.updated_at)::timestamptz
                        WHEN a.status='sent' THEN a.updated_at::timestamptz
                        WHEN j.status='completed' THEN j.finished_at::timestamptz
                        WHEN b.status='completed' THEN to_timestamp(b.updated_at)
                        -- Dispatch stores whole seconds. A successful send ends upload/assignment for this ID.
                        WHEN state_timestamp(d.sent_at)>=date_trunc('second',o.last_seen_at)
                            THEN GREATEST(state_timestamp(d.sent_at),o.last_seen_at)
                        WHEN h.status='pending' AND h.last_error_code IS NULL AND h.attempt_count=0
                            AND h.updated_at ~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
                            THEN h.updated_at::timestamptz END AS recovered_at
                ) evidence
                WHERE o.recovered_at IS NULL AND evidence.recovered_at >= o.last_seen_at
                ORDER BY evidence.recovered_at,o.incident_id
                LIMIT 200
            """).fetchall()
        return failures, recovered

    def related(self, kind, identifier):
        # Only state and identifiers cross this boundary. Operational errors and
        # business payloads from the originating repositories remain private.
        queries = {
            'reports': """SELECT report_id AS id,status,device_id AS line_id,report_id,report_type AS stage
                          FROM report_jobs WHERE report_id=%s""",
            'background': """SELECT task_id AS id,status,device_id AS line_id,NULL AS report_id,kind AS stage
                             FROM background_tasks WHERE task_id=%s""",
            'automation': """SELECT run_id AS id,status,device_id AS line_id,report_id,report_type AS stage
                             FROM scheduled_report_runs WHERE run_id=%s""",
            'email': """SELECT delivery_id AS id,e.status,r.device_id AS line_id,e.report_id,NULL AS stage
                        FROM email_deliveries e JOIN scheduled_report_runs r USING(run_id) WHERE delivery_id=%s""",
            'database-monitor': """SELECT incident_id AS id,COALESCE(end_reason,'down') AS status,
                                  NULL AS line_id,NULL AS report_id,NULL AS stage
                                  FROM database_monitor_incidents WHERE incident_id=%s""",
            'dispatch': """SELECT dispatch_id AS id,status,NULL AS line_id,NULL AS report_id,stage
                           FROM cutting_dispatches WHERE dispatch_id=%s""",
        }
        with self.connect() as connection:
            self._limits(connection)
            row = connection.execute(queries[kind], (identifier,)).fetchone()
            return dict(kind=kind, **row) if row else None
