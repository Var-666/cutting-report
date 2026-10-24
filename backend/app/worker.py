"""Independent background process: PYTHONPATH=backend python -m app.worker."""
from __future__ import annotations

import argparse
from collections import deque
from datetime import date, datetime
import json
import logging
import signal
from threading import Event, Lock
from time import monotonic, time
from zoneinfo import ZoneInfo

from app.config import settings
from app.database.coordination import CoordinationRepository
from app.database.sqlite import SQLiteDatabase, ExecutionLeaseLost, execution_scope
from app.models.domain import ReportFilter
from app.services.application_runtime import build_runtime
from app.services.llm_service import background_llm_work, generation_priority
from app.services.leadership_report_service import _previous_month
from app.services.worker_status import background_ready
from app.workers.bounded_executor import BoundedExecutor
from app.workers.report_worker import ReportWorker


logger = logging.getLogger(__name__)


class WorkerProcess:
    LEASE_SECONDS = 30

    def __init__(self, configuration, runtime=None) -> None:
        self.settings = configuration
        self.runtime = runtime or build_runtime(configuration, role="worker")
        self.repository = self.runtime.coordination
        self.owner = None
        self.stop_event = Event()
        self.analysis = BoundedExecutor(10, 0, "background-analysis")
        self.operations = BoundedExecutor(4, 0, "background-operation")
        self._lock = Lock()
        self._running = {}
        self._completed = deque()
        self.report_worker = None
        self._next_heartbeat = 0.0

    def scope(self):
        return execution_scope(self.settings.metadata_db_path, "worker", self.owner)

    def start(self) -> None:
        self.owner = self.repository.acquire("worker", ttl=self.LEASE_SECONDS)
        if self.owner is None:
            raise RuntimeError("已有有效 Worker 租约，拒绝启动重复消费者")
        try:
            with self.scope():
                self.repository.recover_tasks(self.owner)
                self.runtime.automation.recover_interrupted_deliveries()
                for device in self.runtime.devices.values():
                    history = device.energy_history_worker
                    if history.enabled:
                        history.repository.recover_history_queue(history.source_key, history._now())
                        today = history.service.now().date()
                        from app.workers.energy_history_worker import _shift_month
                        history.schedule_range(_shift_month(today.replace(day=1), -11), today)
                self.report_worker = ReportWorker(
                    self.settings, self.runtime.metadata,
                    automation_service=self.runtime.automation_service, runtime=self.runtime.automation_runtime,
                    pipelines={key: value.pipeline for key, value in self.runtime.devices.items()},
                    execution_context=self.scope,
                )
                self.report_worker.start()
            self._heartbeat()
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
        status["device_ids"] = list(self.runtime.devices)
        diagnostics = getattr(self.runtime, "configuration_diagnostics", None)
        if diagnostics is not None:
            try:
                status["configuration"] = diagnostics.observed(self.owner)
            except Exception:
                status["configuration"] = {"error": "配置快照暂不可用"}
        if not self.repository.renew("worker", self.owner, ttl=self.LEASE_SECONDS, status=status):
            raise ExecutionLeaseLost("Worker 租约失效，停止提交新任务")
        self._next_heartbeat = monotonic() + 5

    def tick(self) -> None:
        if monotonic() >= self._next_heartbeat:
            self._heartbeat()
        with self.scope():
            self._flush_completions()
            self._schedule()
            self.repository.admit_deferred()
            for is_analysis, executor in ((False, self.operations), (True, self.analysis)):
                while executor.available() and not self.stop_event.is_set():
                    task = self.repository.claim_task(self.owner, analysis=is_analysis)
                    if task is None:
                        break
                    with self._lock:
                        self._running[task["task_id"]] = (task, time())
                    try:
                        executor.submit(self._execute, task)
                    except Exception:
                        logger.exception("background executor admission failed: task=%s", task["task_id"])
                        with self._lock:
                            self._completed.append((task, "EXECUTOR_ADMISSION"))
                        break

    def _schedule(self) -> None:
        now = datetime.now(ZoneInfo(self.settings.timezone))
        for device_id, device in self.runtime.devices.items():
            if device.current_report_refresh_worker.enabled:
                interval = max(30, device.settings.report_background_refresh_seconds)
                self.repository.schedule("current_refresh", device_id, {"date": now.date().isoformat()}, interval)
            leadership = device.leadership_refresh_worker
            if leadership.enabled:
                month = now.date().replace(day=1)
                self.repository.schedule("leadership_refresh", device_id,
                    {"date": month.isoformat(), "bootstrap": leadership.service.needs_bootstrap(month)},
                    max(30, device.settings.report_background_refresh_seconds))
                if now.hour >= device.settings.leadership_report_reconcile_hour:
                    # The current month is already scheduled by the periodic refresh.
                    for offset in range(1, 3):
                        selected = _previous_month(month, offset)
                        self.repository.enqueue("leadership_refresh", device_id, {"date": selected.isoformat(), "bootstrap": False},
                            key=f"reconcile:{device_id}:{now.date()}:{selected}")
            history = device.energy_history_worker
            if (history.enabled and (history._pending_finish is not None or history.repository.history_ready(history.source_key, history._now()))
                    and not self.repository.active("energy_sync", device_id, {})):
                self.repository.enqueue("energy_sync", device_id, {})
            if device.alarm_worker.enabled:
                self.repository.schedule("alarm", device_id, {}, max(30, device.settings.alarm_monitor_interval_seconds))
        self.repository.schedule("retention", "", {}, max(1, self.settings.retention_cleanup_interval_hours) * 3600)
        if self.runtime.download_cleanup_worker.enabled:
            self.repository.schedule("download_cleanup", "", {}, 86400)

    @background_llm_work()
    def _execute(self, task: dict) -> None:
        error = None
        try:
            if task["kind"].startswith("analysis_"):
                priority = Event()
                priority.set()
                with generation_priority(priority):
                    self._handle(task)
            else:
                self._handle(task)
        except Exception as exc:
            error = type(exc).__name__
            logger.exception("background task failed: task=%s kind=%s device=%s", task["task_id"], task["kind"], task["device_id"])
        finally:
            with self._lock:
                self._completed.append((task, error))

    def _flush_completions(self) -> None:
        while True:
            with self._lock:
                item = self._completed[0] if self._completed else None
            if item is None:
                return
            task, error = item
            self.repository.finish_task(task, self.owner, error)
            with self._lock:
                self._completed.popleft()
                self._running.pop(task["task_id"], None)

    def _handle(self, task: dict) -> None:
        kind, payload = task["kind"], task["payload"]
        if kind == "retention":
            if self.runtime.retention_worker.enabled:
                self.runtime.retention_service.cleanup_expired()
            self.repository.cleanup_completed()
            return
        if kind == "download_cleanup":
            self.runtime.file_downloads.cleanup_expired()
            return
        device = self.runtime.devices[task["device_id"]]
        current = device.current_report_refresh_worker
        if kind == "current_refresh":
            current.run_once(date.fromisoformat(payload["date"]))
            device.query_service.cache_revision.publish()
        elif kind == "leadership_refresh":
            device.leadership_refresh_worker._refresh(date.fromisoformat(payload["date"]), payload["bootstrap"])
        elif kind == "analysis_leadership":
            device.leadership_refresh_worker._analyze(date.fromisoformat(payload["date"]))
        elif kind == "analysis_workshop":
            current._analyze_workshop(date.fromisoformat(payload["date"]))
        elif kind == "analysis_equipment":
            selected = date.fromisoformat(payload["date"])
            equipment = device.query_service.fetch_equipment_data(ReportFilter(report_date=selected))
            task_report = device.energy_period_service.get_task_day(selected, equipment.records)
            current._analyze_equipment(selected, equipment.records, task_report)
        elif kind == "alarm":
            device.alarm_monitor_service.run_once(datetime.now(ZoneInfo(self.settings.timezone)))
        elif kind == "energy_sync":
            history = device.energy_history_worker
            history._flush_pending_finish()
            claim = history._claim()
            if claim:
                history._process(claim)
        else:
            raise ValueError(f"未知后台任务类型: {kind}")

    def run(self) -> None:
        self.start()
        try:
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except ExecutionLeaseLost:
                    logger.exception("Worker execution lease lost")
                    break
                except Exception:
                    logger.exception("Worker scheduler cycle failed")
                self.stop_event.wait(1)
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        deadline = monotonic() + 5
        if self.report_worker:
            self.report_worker.stop()
        operations_stopped = self.operations.stop(max(0, deadline - monotonic()))
        analysis_stopped = self.analysis.stop(max(0, deadline - monotonic()))
        report_stopped = not self.report_worker or not any(thread.is_alive() for thread in self.report_worker._threads())
        if self.owner and operations_stopped and analysis_stopped and report_stopped:
            try:
                with self.scope():
                    self._flush_completions()
                self.repository.release("worker", self.owner)
            except ExecutionLeaseLost:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        try:
            status = CoordinationRepository(SQLiteDatabase(settings.metadata_db_path, timeout_seconds=1)).worker_status()
            print(json.dumps({key: value for key, value in status.items() if key != "configuration"}, ensure_ascii=False))
            return 0 if status.get("state") == "running" and background_ready(status) else 1
        except Exception:
            return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker = WorkerProcess(settings)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: worker.stop_event.set())
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
