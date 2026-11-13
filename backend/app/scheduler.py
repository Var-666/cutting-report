"""Single production-period Scheduler; never consumes business tasks."""
from __future__ import annotations
import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
import signal
from threading import Event
from time import monotonic, time
from zoneinfo import ZoneInfo

from app.config import Settings, settings
from app.database.postgres import execution_scope, ExecutionLeaseLost
from app.services.application_runtime import build_runtime
from app.services.database_runtime import database_context
from app.services.production_reports import report_providers
from app.services.worker_status import role_health

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class _Schedule:
    interval: float
    source: str
    deadline: float


class SchedulerProcess:
    LEASE_SECONDS = 30

    def __init__(self, configuration: Settings, runtime=None):
        self.settings = configuration
        self.runtime = runtime or build_runtime(configuration, role='scheduler')
        self.repository = self.runtime.coordination
        self.owner = None
        self.stop_event = Event()
        self._closed = False
        self._next_heartbeat = 0.0
        self._next_due = {}
        self._reconciled = {}
        self._seen_schedules = set()
        self._seen_reconciliations = set()
        self._clock_offset = None

    def scope(self):
        return execution_scope(self.runtime.database, 'scheduler', self.owner)

    def start(self):
        self.owner = self.repository.acquire('scheduler', ttl=self.LEASE_SECONDS)
        if self.owner is None:
            self.stop()
            raise RuntimeError('已有有效 Scheduler 租约')
        self._heartbeat()

    def _heartbeat(self):
        status = {'scheduler_alive': True, 'stalled_workers': []}
        try:
            status['configuration'] = self.runtime.configuration_diagnostics.observed(self.owner)
        except Exception:
            status['configuration'] = {'error': '配置快照暂不可用'}
        if not self.repository.renew('scheduler', self.owner, ttl=self.LEASE_SECONDS, status=status):
            raise ExecutionLeaseLost('Scheduler 租约失效')
        self._next_heartbeat = monotonic() + 5

    def tick(self):
        if monotonic() >= self._next_heartbeat:
            self._heartbeat()
        with self.scope():
            self._schedule()

    def _schedule(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.timezone))
        context = database_context(self.runtime)
        offset = time() - monotonic()
        if self._clock_offset is not None and abs(offset - self._clock_offset) > 1:
            self._next_due.clear()  # Rebase wall-clock persistence after a system clock step.
        self._clock_offset = offset
        self._seen_schedules.clear()
        self._seen_reconciliations.clear()
        try:
            for line in context.lines.values():
                if line.config.enabled and line.background is not None:
                    line.background.schedule_background(self, now)
            for line_id, provider in report_providers(context):
                for payload, interval in provider.refresh_plan():
                    self._schedule_snapshot(line_id, payload, interval)
            self.schedule("retention", "", {}, max(1, self.settings.retention_cleanup_interval_hours) * 3600)
            self.schedule("database_monitor", "", {}, 60)
            if self.runtime.download_cleanup_worker.enabled:
                self.schedule("download_cleanup", "", {}, 86400)
        finally:
            self._next_due = {key: value for key, value in self._next_due.items() if key in self._seen_schedules}
            self._reconciled = {key: value for key, value in self._reconciled.items() if key in self._seen_reconciliations}

    def _waiting(self, key: str, interval: float, source: str) -> bool:
        self._seen_schedules.add(key)
        state = self._next_due.get(key)
        return bool(state and state.interval == interval and state.source == source and monotonic() < state.deadline)

    def _remember_due(self, key: str, interval: float, source: str, due: float) -> None:
        self._next_due[key] = _Schedule(interval, source, monotonic() + max(0, due - time()))

    def schedule(self, kind: str, line_id: str, payload: dict | Callable[[], dict], interval: float, *, source: str = "") -> None:
        key = f"{kind}:{line_id}"
        if self._waiting(key, interval, source):
            return
        due = self.repository.next_due(key, interval)
        if due <= time():
            due = self.repository.schedule(kind, line_id, payload() if callable(payload) else payload, interval)
        self._remember_due(key, interval, source, due)

    def enqueue_once(self, kind: str, line_id: str, payload: dict, *, key: str) -> None:
        self._seen_reconciliations.add(key)
        source = payload.get('source_fingerprint', '')
        if self._reconciled.get(key) == source:
            return
        if self.repository.enqueue(kind, line_id, payload, key=key) or self.repository.task_exists(key):
            self._reconciled[key] = source

    def _schedule_snapshot(self, line_id: str, payload: dict, interval: float) -> None:
        key = self.repository.snapshot_task_key(line_id, payload)
        source = payload['source_fingerprint']
        if self._waiting(key, interval, source):
            return
        due = self.repository.snapshot_refresh_due(line_id, payload, interval)
        if due <= time():
            self.repository.request_snapshot_refresh(line_id, payload, interval=interval)
            due = self.repository.snapshot_refresh_due(line_id, payload, interval)
        self._remember_due(key, interval, source, due)

    def run(self):
        try:
            self.start()
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except ExecutionLeaseLost:
                    break
                except Exception:
                    logger.exception('Scheduler cycle failed')
                self.stop_event.wait(1)
        finally:
            self.stop()

    def stop(self):
        if self._closed: return
        self._closed = True
        self.stop_event.set()
        try:
            if self.owner: self.repository.release('scheduler', self.owner)
        finally:
            self.runtime.database_runtime.close()
            self.runtime.database.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--health', action='store_true')
    args = parser.parse_args()
    if args.health: return role_health(settings, 'scheduler')
    logging.basicConfig(level=logging.INFO)
    process = SchedulerProcess(settings)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: process.stop_event.set())
    process.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
