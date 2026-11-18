"""Observe existing role leases and business outcomes without touching production sources."""
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
import logging
from queue import Empty
from threading import Event, Lock, Thread
from time import monotonic, time

from app.core.operations_logging import event, log_request_id, prune_logs, safe_id
from app.database.operations import OperationsRepository, fingerprint
from app.models.operations import LogFilters
from app.services.operations_log_query import OperationsLogQuery
from app.services.worker_status import role_ready

logger = logging.getLogger(__name__)
BUSINESS_ERRORS = {
    ('background', 'failed'): ('TASK_FAILED', '后台任务执行失败', 'worker', 'ERROR'),
    ('background', 'deferred'): ('TASK_REPEATED_FAILURE', '后台任务多次失败，等待重试', 'worker', 'WARNING'),
    ('background', 'blocked'): ('TASK_BLOCKED', '后台任务已阻塞', 'worker', 'ERROR'),
    ('reports', 'failed'): ('REPORT_GENERATION_FAILED', '报表生成失败', 'worker', 'ERROR'),
    ('automation', 'failed'): ('AUTOMATION_RUN_FAILED', '自动化计划执行失败', 'automation', 'ERROR'),
    ('email', 'failed'): ('EMAIL_DELIVERY_FAILED', '邮件发送失败', 'automation', 'ERROR'),
    ('email', 'unknown'): ('EMAIL_RESULT_UNKNOWN', '邮件发送结果待核对', 'automation', 'WARNING'),
    ('email', 'retry_wait'): ('EMAIL_DELIVERY_RETRY', '邮件暂时发送失败，等待重试', 'automation', 'WARNING'),
    ('database-monitor', 'down'): ('DATABASE_TCP_DOWN', '监控目标 TCP 端口持续不可连接', 'worker', 'ERROR'),
}
BUSINESS_ERROR_CODES = {key: fields[0] for key, fields in BUSINESS_ERRORS.items()}
RECOVERY_EVIDENCE = {
    'database-monitor': '原监控目标连续两次 TCP 检测成功',
    'email': '原邮件发送记录已取得 SMTP 接收证据',
    'reports': '原报表任务已完成',
    'automation': '原自动化运行已发送',
    'background': '原后台任务已完成',
    'services.cutting_dispatch_service': '原派发任务已取得魔盒指派成功记录',
    'workers.energy_history_worker': '对应能耗来源和月份的同步任务已成功完成',
}
HTTP_FAILURE_WINDOW = 60
HTTP_FAILURE_THRESHOLD = 3
HTTP_FAILURE_MAX_SCOPES = 512
TCP_FAILURE_MESSAGES = frozenset({'连接超时', '端口拒绝连接', '网络或主机不可达', '连接失败'})


class OperationsService:
    def __init__(self, settings, database, coordination, reader, monitors, diagnostics, role):
        self.settings = settings
        self.repository = OperationsRepository(database)
        self.coordination = coordination
        self.reader = reader
        self.monitors = monitors
        self.diagnostics = diagnostics
        self.role = role
        self.logs = OperationsLogQuery(settings.logs_dir, settings.operations_log_retention_days)
        self._browser_window = -1
        self._browser_seen = set()
        self._browser_lock = Lock()
        self._next_totals = 0.0
        self._totals = None
        self._totals_lock = Lock()
        self._http_failures = OrderedDict()

    def repeated_http_failure(self, route, line_id, source_id, status):
        """Called by Web middleware on its event loop, without database or file I/O."""
        key = (route, line_id, source_id)
        if 200 <= status < 300:
            self._http_failures.pop(key, None)
            return False
        if status not in (503, 504):
            return False
        now = monotonic()
        first, count = self._http_failures.pop(key, (now, 0))
        if now - first >= HTTP_FAILURE_WINDOW:
            first, count = now, 0
        count += 1
        self._http_failures[key] = (first, count)
        if len(self._http_failures) > HTTP_FAILURE_MAX_SCOPES:
            self._http_failures.popitem(last=False)
        return count >= HTTP_FAILURE_THRESHOLD

    def status(self):
        now = datetime.now(UTC)
        worker = self.reader.status()
        available = worker.get('database_available')
        states = []
        try:
            web = self.coordination.worker_status('web') if available else {}
        except Exception:
            web = {}
        entries = [(role, None, worker.get('roles', {}).get(role, {})) for role in ('scheduler', 'worker', 'automation')]
        entries.insert(0, ('web', None, web))
        entries.extend(('worker', row['instance_id'], row) for row in worker.get('consumer_instances', []))
        for role, instance, row in entries:
            detail = row.get('state', 'unavailable')
            ready = detail == 'running' if role == 'web' else role_ready(role, row)
            age = row.get('heartbeat_age_seconds')
            if role == 'worker' and instance is None and available and worker.get('consumer_capacity_shortfall', 0):
                detail = 'capacity_shortfall'
            elif detail == 'running' and not ready:
                detail = 'degraded'
            state = 'healthy' if ready else 'unknown' if not available or detail == 'unavailable' else 'abnormal'
            configuration = 'unknown'
            snapshot = row.get('configuration')
            # A failed inspection may publish a placeholder without a loaded snapshot.
            if row.get('state') in ('running', 'stalled') and isinstance(snapshot, dict):
                if role == 'web' and snapshot.get('load_state') in ('healthy', 'abnormal'):
                    configuration = snapshot['load_state']
                elif (role != 'worker' or instance) and isinstance(snapshot.get('items'), dict):
                    configuration = 'abnormal' if snapshot.get('error') or snapshot.get('database_error') else 'healthy'
            states.append(dict(role=role, instance_id=instance, state=state, detail=detail,
                               heartbeat_age_seconds=age,
                               heartbeat_at=now - timedelta(seconds=age) if age is not None else None,
                               stalled_workers=row.get('stalled_workers', []), configuration_state=configuration))
        monitors = work = None
        if available:
            try:
                monitors = self.monitors.list()
                work, _ = self._work(now)
            except Exception:
                pass
        return dict(observed_at=now, roles=states, postgres='healthy' if available else 'abnormal' if available is False else 'unknown',
                    consumers_ready=worker.get('consumer_running_count'), consumers_expected=self.settings.consumer_replicas,
                    monitors=monitors, work=work)

    def overview(self):
        runtime = self.status()
        count = recent = recurring = None
        if runtime['postgres'] == 'healthy':
            try:
                count, recent, recurring = self.repository.summary()
            except Exception:
                pass
        # Concurrent dashboard requests share the result of one bounded file scan.
        if monotonic() >= self._next_totals and self._totals_lock.acquire(blocking=False):
            try:
                self._totals = self.logs.query(LogFilters(), totals=True)
                self._next_totals = monotonic() + 30
            finally:
                self._totals_lock.release()
        return dict(open_incidents=count, recent=recent, recurring=recurring, log_totals=self._totals, runtime=runtime)

    def _work(self, now):
        threshold = max(self.settings.worker_stall_timeout_seconds, 120)
        return self.repository.work_status((now - timedelta(hours=24)).isoformat(), threshold)

    @staticmethod
    def _item(now, code, message, *, component='health', role='web', **context):
        return dict(timestamp=now, level='ERROR', error_code=code, message=message,
                    component=component, role=role, **context)

    def discover(self):
        now = datetime.now(UTC)
        runtime = self.status()
        if runtime['postgres'] != 'healthy':
            return
        observations = []
        for row in runtime['roles']:
            role, instance = row['role'], row['instance_id']
            configuration = row['configuration_state']
            if role != 'worker' or instance:
                item = self._item(now, 'CONFIGURATION_LOAD_FAILED', '运行角色配置加载失败',
                                  component='configuration', role=role, instance_id=instance, resource_id=instance)
                loaded = None if configuration == 'unknown' else configuration == 'healthy'
                observations.append((item, loaded, '同一角色实例连续两次上报配置已成功加载'))
            if row['state'] == 'unknown' or (instance and row['detail'] == 'stopped'):
                continue
            code = 'CONSUMER_CAPACITY' if role == 'worker' and not instance else 'PROCESS_UNHEALTHY'
            item = self._item(now, code, '正常 Consumer 数量不足或执行异常' if code == 'CONSUMER_CAPACITY'
                              else '进程心跳缺失、租约失效或执行停滞',
                              role=role, instance_id=instance, resource_id=instance,
                              stage=row['detail'])
            observations.append((item, row['state'] == 'healthy', '连续两次检查获得有效心跳及角色就绪状态'))
            if role in ('scheduler', 'automation') or instance:
                lease = self._item(now, 'EXECUTION_LEASE_LOST', '执行租约失效', component='leases', role=role,
                                   resource_id=instance, recovery_only=True)
                observations.append((lease, row['state'] == 'healthy', '同一角色实例连续两次取得有效心跳并就绪'))
        _, pressure = self._work(now)
        for key, code, message in [
            ('queue_late', 'QUEUE_BACKLOG', '后台队列持续积压'),
            ('task_stalled', 'TASK_STALLED', '后台任务长时间未完成'),
        ]:
            observations.append((self._item(now, code, message, role='worker'), not pressure[key],
                                 '连续两次检查未发现超时的队列记录'))
        _, invalid, errors = self.diagnostics.target()
        observations.append((self._item(now, 'CONFIGURATION_INVALID', '目标配置校验失败'),
                             not (invalid or errors), '连续两次校验目标配置成功'))
        observations.append((self._item(now, 'POSTGRES_UNAVAILABLE', 'PostgreSQL 状态库不可用', component='postgres'),
                             True, '连续两次读取状态库成功'))
        failures, recoveries = self.repository.business_status((now - timedelta(days=1)).isoformat(), BUSINESS_ERROR_CODES)
        for row in recoveries:
            row['evidence'] = '原邮件发送记录已按服务器证据人工核对' if row['manually_verified'] else RECOVERY_EVIDENCE[row['component']]
        business = []
        for row in failures:
            kind = row['kind']
            code, message, role, level = BUSINESS_ERRORS[kind, row['status']]
            if kind == 'database-monitor' and row['last_error'] in TCP_FAILURE_MESSAGES:
                message = f'{message}，最近一次{row["last_error"]}'
            item = self._item(row['stamp'], code, message, component=kind, role=role,
                              resource_id=safe_id(row['id']), related_kind=kind,
                              task_id=safe_id(row['id']) if kind == 'background' else None,
                              report_id=safe_id(row['report_id']), stage=safe_id(row['stage']),
                              request_id=log_request_id(row['request_id']),
                              source_id=safe_id(row['device_id']) if kind == 'database-monitor' else None,
                              line_id=safe_id(row['device_id']) if kind != 'database-monitor' else None)
            business.append({**item, 'level': level, 'source_first_at': row.get('source_first_at'),
                             'source_count': row.get('source_count')})
            if row.get('verified_at'):
                recoveries.append(dict(fingerprint=fingerprint(item), recovered_at=row['verified_at'],
                                       evidence=RECOVERY_EVIDENCE[kind]))
        self.repository.observe(observations, business, recoveries, now)

    def browser_issue(self, actor, data):
        window = int(time()) // 60
        with self._browser_lock:
            if window != self._browser_window:
                self._browser_seen.clear()
                self._browser_window = window
            key = (actor, data.kind)
            if key in self._browser_seen or len(self._browser_seen) >= 60:
                return False
            self._browser_seen.add(key)
        event(logger, logging.WARNING, '前端检测到运行错误', error_code=f'BROWSER_{data.kind.upper()}',
              request_id=data.request_id)
        return True

    def cleanup(self, now, limit):
        self.repository.cleanup(now - timedelta(days=self.settings.operations_incident_retention_days), limit)
        prune_logs(self.settings.logs_dir, self.settings.operations_log_retention_days)

    def collect(self, logs):
        return OperationsCollector(self, logs)


class OperationsCollector:
    """Bounded event flushing outside business threads and their execution scopes."""
    BATCH_ATTEMPTS = 3

    def __init__(self, service, logs):
        self.service = service
        self.logs = logs
        self.stop_event = Event()
        self.thread = Thread(target=self._run, daemon=True, name='operations-collector')
        self.owner = None
        self._failed = set()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def stop(self):
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.thread.join(3)

    def _attempt(self, stage, operation, *args):
        try:
            operation(*args)
        except Exception:
            if stage not in self._failed:
                event(logger, logging.WARNING, '运维状态采集或事件写入失败', error_code='OPERATIONS_WRITE_FAILED',
                      stage=stage, exc_info=True)
            self._failed.add(stage)
            return False
        self._failed.discard(stage)
        return True

    def _heartbeat(self):
        if not self.owner:
            self.owner = self.service.coordination.acquire('web', ttl=30)
        if not self.owner:
            return
        configuration = 'unknown'
        try:
            configuration = self.service.diagnostics.load_state()
        except Exception:
            pass
        if not self.service.coordination.renew('web', self.owner, ttl=30,
                                               status={'configuration': {'load_state': configuration}}):
            self.owner = None

    def _run(self):
        next_discovery = next_heartbeat = 0.0
        pending = []
        attempts = 0
        try:
            while True:
                if not pending:
                    for _ in range(32):
                        try:
                            pending.append(self.logs.incidents.get_nowait())
                        except Empty:
                            break
                stopping = self.stop_event.is_set()
                if not stopping:
                    now = monotonic()
                    if self.service.role == 'web' and now >= next_heartbeat:
                        next_heartbeat = now + 5
                        self._attempt('heartbeat', self._heartbeat)
                    if self.service.role in ('scheduler', 'web') and now >= next_discovery:
                        next_discovery = now + 30
                        self._attempt('discovery', self.service.discover)
                if pending:
                    if self._attempt('incident_batch', self.service.repository.record, pending):
                        pending.clear()
                        attempts = 0
                    else:
                        attempts += 1
                        if stopping or attempts >= self.BATCH_ATTEMPTS:
                            event(logger, logging.WARNING, '运维事件批次未能写入状态库，保留本地日志',
                                  error_code='OPERATIONS_BATCH_DROPPED')
                            pending.clear()
                            attempts = 0
                            if stopping:
                                break
                if stopping:
                    if self.logs.incidents.empty():
                        break
                else:
                    self.stop_event.wait(5 if pending else 2)
        finally:
            if self.owner:
                try:
                    self.service.coordination.release('web', self.owner)
                except Exception:
                    pass
