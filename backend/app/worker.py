"""Independent background process: PYTHONPATH=backend python -m app.worker."""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
import logging
import signal
from threading import Event, Lock
from time import monotonic, time
from zoneinfo import ZoneInfo

from app.config import Settings, settings, consumer_identity
from app.core.sql_capacity import sql_identity
from app.database.postgres import ExecutionLeaseLost, execution_scope
from app.services.application_runtime import ApplicationRuntime, build_runtime
from app.services.database_runtime import database_context
from app.services.llm_service import background_llm_work, generation_priority
from app.services.worker_status import role_health
from app.services.production_reports import report_provider
from app.workers.bounded_executor import BoundedExecutor
from app.workers.report_worker import ReportWorker


logger = logging.getLogger(__name__)


class TaskSourceUnavailable(RuntimeError):
    pass


class WorkerProcess:
    LEASE_SECONDS = 30
    DEFERRED_SCAN_SECONDS = 5

    def __init__(self, configuration: Settings, runtime: ApplicationRuntime | None = None) -> None:
        self.settings = configuration
        self.instance_id = consumer_identity(configuration)
        self.lease_name = f'worker:{self.instance_id}'
        self.runtime = runtime or build_runtime(configuration, role="worker")
        self.repository = self.runtime.coordination
        self.owner = None
        self._closed = False
        self.stop_event = Event()
        self.analysis = BoundedExecutor(configuration.consumer_analysis_concurrency, 0, "background-analysis")
        self.operations = BoundedExecutor(configuration.consumer_operation_concurrency, 0, "background-operation")
        self._lock = Lock()
        self._running = {}
        self._completed = deque()
        self.report_worker = None
        self._next_heartbeat = 0.0
        self._next_admission = 0.0
        self._next_recovery = 0.0
        self._initialized_lines = {}

    def scope(self):
        return execution_scope(self.runtime.database, self.lease_name, self.owner)

    def start(self) -> None:
        try:
            self.owner = self.repository.acquire(self.lease_name, ttl=self.LEASE_SECONDS)
            if self.owner is None:
                raise RuntimeError(f"已有有效 Consumer 实例租约：{self.instance_id}")
            with self.scope():
                self.repository.recover_tasks(self.owner)
                self._initialize_lines()
                self.report_worker = ReportWorker(
                    self.settings, self.runtime.metadata,
                    automation_service=self.runtime.automation_service,
                    pipelines=lambda: {key: value.pipeline for key, value in database_context(self.runtime).lines.items() if value.config.enabled and value.pipeline is not None},
                    execution_context=self.scope,
                )
                self.report_worker.start()
            self._heartbeat()
            self._next_recovery = monotonic() + 5
        except BaseException:
            self.stop()
            raise

    def _heartbeat(self) -> None:
        status = self.report_worker.status() if self.report_worker else {"report_alive": False}
        with self._lock:
            running = list(self._running.values())
        stalled = [f"{task['kind']}:{task['device_id']}" for task, started in running
                   if time() - started >= self.settings.worker_stall_timeout_seconds]
        status["stalled_workers"] = [*status.get("stalled_workers", []), *stalled]
        status["background_queue"] = self.repository.task_counts()
        status["active_tasks"] = len(running)
        status['instance_id'] = self.instance_id
        status['lease_name'] = self.lease_name
        status['operation_capacity'] = self.settings.consumer_operation_concurrency
        status['analysis_capacity'] = self.settings.consumer_analysis_concurrency
        context = database_context(self.runtime)
        status["device_ids"] = list(context.lines)
        status["sql_resources"] = context.database_sources.capacity.status(context.database_sources.configs())
        diagnostics = getattr(self.runtime, "configuration_diagnostics", None)
        if diagnostics is not None:
            try:
                status["configuration"] = diagnostics.observed(self.owner)
            except Exception:
                status["configuration"] = {"error": "配置快照暂不可用"}
        if not self.repository.renew(self.lease_name, self.owner, ttl=self.LEASE_SECONDS, status=status):
            raise ExecutionLeaseLost("Worker 租约失效，停止提交新任务")
        self._next_heartbeat = monotonic() + 5

    def tick(self) -> None:
        if monotonic() >= self._next_heartbeat:
            self._heartbeat()
        with self.scope():
            completed = self._flush_completions()
            self._initialize_lines()
            if monotonic() >= self._next_recovery:
                self.repository.recover_tasks(self.owner)
                self.runtime.metadata.recover_interrupted_jobs()
                for line in database_context(self.runtime).lines.values():
                    if line.config.enabled and line.background is not None:
                        line.background.recover_background()
                self._next_recovery = monotonic() + 5
            if completed or monotonic() >= self._next_admission:
                self.repository.admit_deferred()
                self._next_admission = monotonic() + self.DEFERRED_SCAN_SECONDS
            for is_analysis, executor in ((False, self.operations), (True, self.analysis)):
                while executor.available() and not self.stop_event.is_set():
                    task = self.repository.claim_task(self.owner, analysis=is_analysis)
                    if task is None:
                        break
                    self._next_admission = 0.0  # Claiming releases pending-queue capacity.
                    with self._lock:
                        self._running[task["task_id"]] = (task, time())
                    try:
                        executor.submit(self._execute, task)
                    except Exception:
                        logger.exception("background executor admission failed: task=%s", task["task_id"])
                        with self._lock:
                            self._completed.append((task, "EXECUTOR_ADMISSION"))
                        break

    @background_llm_work()
    def _execute(self, task: dict) -> None:
        error = None
        started = monotonic()
        snapshot = task['kind'] == 'report_snapshot_refresh'
        report_type = None
        try:
            if snapshot:
                report_type = task['payload'].get('report_type')
                query = task['payload'].get('query') or {}
                logger.info('report snapshot refresh started: task=%s device=%s report_type=%s start=%s end=%s',
                            task['task_id'], task['device_id'], report_type,
                            query.get('start'), query.get('end'))
            if task["kind"].startswith("analysis_"):
                priority = Event()
                priority.set()
                with generation_priority(priority):
                    self._handle(task)
            else:
                self._handle(task)
        except Exception as exc:
            error = 'SOURCE_UNVERIFIED' if isinstance(exc, TaskSourceUnavailable) else type(exc).__name__
            logger.exception("background task failed: task=%s kind=%s device=%s", task["task_id"], task["kind"], task["device_id"])
        finally:
            if snapshot:
                logger.info('report snapshot refresh finished: task=%s device=%s report_type=%s outcome=%s elapsed_ms=%s',
                            task['task_id'], task['device_id'], report_type,
                            'failed' if error else 'completed', round((monotonic() - started) * 1000))
            with self._lock:
                self._completed.append((task, error))

    def _flush_completions(self) -> int:
        completed = 0
        while True:
            with self._lock:
                item = self._completed[0] if self._completed else None
            if item is None:
                return completed
            task, error = item
            self.repository.finish_task(task, self.owner, error)
            with self._lock:
                self._completed.popleft()
                self._running.pop(task["task_id"], None)
            completed += 1

    def _initialize_lines(self):
        context = database_context(self.runtime)
        active = {}
        for line_id, line in context.lines.items():
            if line.config.enabled and line.background is not None:
                if self._initialized_lines.get(line_id) is not line.background:
                    line.background.start_background()
                line.background.flush_background_results()
                active[line_id] = line.background
        self._initialized_lines = active

    def _handle(self, task: dict) -> None:
        with sql_identity(task["device_id"] or None):
            kind, payload = task["kind"], task["payload"]
            if kind == "retention":
                if self.runtime.retention_worker.enabled:
                    self.runtime.retention_service.cleanup_expired()
                self.repository.cleanup_completed()
                return
            if kind == "download_cleanup":
                database_context(self.runtime).file_downloads.cleanup_expired()
                return
            if kind == 'report_snapshot_refresh':
                context = database_context(self.runtime)
                if task['device_id'] not in context.lines or not context.lines[task['device_id']].config.enabled:
                    return
                provider = report_provider(context, task['device_id'])
                # Queued work from an old connection must not populate the new source's snapshots.
                if not payload.get('source_fingerprint') or payload['source_fingerprint'] != provider.source_identity():
                    raise TaskSourceUnavailable('快照任务来源缺失或已变化，必须重新入队')
                provider.refresh_summary(payload['report_type'], payload['query'], requested_at=task['created_at'])
                return
            line = database_context(self.runtime).lines.get(task["device_id"])
            if line is None or not line.config.enabled:
                return  # Retired instance: keep historical work, never redirect it to another line.
            if line.background is None:
                raise ValueError("产线不支持此后台任务")
            fingerprint = payload.get("source_fingerprint")
            if not fingerprint or fingerprint != line.settings.source_fingerprint:
                raise TaskSourceUnavailable('任务来源缺失或已变化，必须核对后重新入队')
            line.background.handle_background(kind, payload)

    def run(self) -> None:
        try:
            self.start()
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except ExecutionLeaseLost:
                    logger.exception("Worker execution lease lost")
                    break
                except Exception:
                    logger.exception("Worker consumer cycle failed")
                self.stop_event.wait(1)
        finally:
            self.stop()

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop_event.set()
        deadline = monotonic() + 5
        if self.report_worker:
            self.report_worker.stop()
        operations_stopped = self.operations.stop(max(0, deadline - monotonic()))
        analysis_stopped = self.analysis.stop(max(0, deadline - monotonic()))
        try:
            self.runtime.database_runtime.close()
            report_stopped = not self.report_worker or not any(thread.is_alive() for thread in self.report_worker._threads())
            if self.owner and operations_stopped and analysis_stopped and report_stopped:
                try:
                    with self.scope():
                        self._flush_completions()
                    self.repository.release(self.lease_name, self.owner)
                except ExecutionLeaseLost:
                    pass
        finally:
            self.runtime.database.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        return role_health(settings, f'worker:{consumer_identity(settings)}')
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = WorkerProcess(settings)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: worker.stop_event.set())
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
