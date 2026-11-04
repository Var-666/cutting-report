"""Independent automation plans/reconciliation and SMTP delivery process."""
from __future__ import annotations
import argparse
from datetime import datetime
import logging
import signal
import threading
from time import monotonic
from zoneinfo import ZoneInfo

from app.config import settings
from app.database.postgres import execution_scope, ExecutionLeaseLost
from app.services.application_runtime import build_runtime
from app.services.worker_status import role_health

logger = logging.getLogger(__name__)


class AutomationProcess:
    LEASE_SECONDS = 30

    def __init__(self, configuration, runtime=None):
        self.settings = configuration
        self.runtime = runtime or build_runtime(configuration, role='automation')
        self.repository = self.runtime.coordination
        self.automation_service = self.runtime.automation_service
        self.automation_runtime = self.runtime.automation_runtime
        self.owner = None
        self.stop_event = threading.Event()
        self._closed = False
        self._status_lock = threading.Lock()
        self._progress = {}
        self.threads = [threading.Thread(target=self._owned, args=(handler,), name=name, daemon=True)
                        for name,handler in [('automation-worker',self._automation_loop),('email-worker',self._email_loop)]]

    def scope(self):
        return execution_scope(self.runtime.database, 'automation', self.owner)

    def _owned(self, handler):
        try:
            with self.scope(): handler()
        except ExecutionLeaseLost:
            self.stop_event.set()

    def _checkpoint(self, name, stage):
        with self._status_lock: self._progress[name] = (stage, monotonic())

    def status(self):
        with self._status_lock: progress = dict(self._progress)
        details = {}
        for name,thread in zip(('automation','email'),self.threads):
            stage,stamp = progress.get(name,('starting',monotonic()))
            age = monotonic()-stamp
            details[name] = {'stage':stage,'progress_age_seconds':round(age,1),
                'stalled':thread.is_alive() and age >= self.settings.worker_stall_timeout_seconds}
        return {'automation_alive':self.threads[0].is_alive(),'email_alive':self.threads[1].is_alive(),
                'progress':details,'stalled_workers':[k for k,v in details.items() if v['stalled']]}

    def start(self):
        self.owner = self.repository.acquire('automation', ttl=self.LEASE_SECONDS)
        if self.owner is None:
            self.stop()
            raise RuntimeError('已有有效 Automation 租约')
        try:
            with self.scope(): self.runtime.automation.recover_interrupted_deliveries()
            for thread in self.threads: thread.start()
            self._heartbeat()
        except BaseException:
            self.stop()
            raise

    def _heartbeat(self):
        status = self.status()
        context = self.runtime.database_runtime.snapshot()
        status['sql_resources'] = context.database_sources.capacity.status(context.database_sources.configs())
        try:
            status['configuration'] = self.runtime.configuration_diagnostics.observed(self.owner)
        except Exception:
            status['configuration'] = {'error': '配置快照暂不可用'}
        if not self.repository.renew('automation',self.owner,ttl=self.LEASE_SECONDS,status=status):
            raise ExecutionLeaseLost('Automation 租约失效')

    def run(self):
        try:
            self.start()
            while not self.stop_event.wait(5):
                try: self._heartbeat()
                except ExecutionLeaseLost: break
                except Exception: logger.exception('Automation heartbeat failed')
        finally: self.stop()

    def stop(self):
        if self._closed: return
        self._closed = True
        self.stop_event.set()
        deadline = monotonic()+5
        for thread in self.threads:
            if thread.ident is not None: thread.join(max(0,deadline-monotonic()))
        try:
            if self.owner and not any(t.is_alive() for t in self.threads): self.repository.release('automation',self.owner)
        finally:
            self.runtime.database_runtime.close()
            self.runtime.database.close()

    def _automation_loop(self) -> None:
        timezone = ZoneInfo(self.settings.timezone)
        next_scan = 0.0
        seen_revision = -1
        while not self.stop_event.is_set():
            try:
                self._checkpoint("automation", "configuration")
                runtime_settings, revision = self.automation_runtime.snapshot()
                if revision != seen_revision:
                    next_scan = 0.0
                    seen_revision = revision
                now = datetime.now(timezone)
                if monotonic() >= next_scan:
                    try:
                        self._checkpoint("automation", "recovering")
                        self.automation_service.reconcile_runs(now)
                    except ExecutionLeaseLost:
                        raise
                    except Exception:
                        logger.exception("automation recovery scan failed")
                    try:
                        self._checkpoint("automation", "scheduling")
                        self.automation_service.process_due_schedules(now)
                    except ExecutionLeaseLost:
                        raise
                    except Exception:
                        logger.exception("automation schedule scan failed")
                    next_scan = monotonic() + runtime_settings.automation_scan_seconds
            except ExecutionLeaseLost:
                raise
            except Exception:
                logger.exception("automation scheduler processing failed")
            finally:
                self._checkpoint("automation", "idle")
            self.stop_event.wait(self.settings.worker_poll_seconds)

    def _email_loop(self) -> None:
        timezone = ZoneInfo(self.settings.timezone)
        while not self.stop_event.is_set():
            try:
                self._checkpoint("email", "delivery")
                if self.automation_service.process_next_email(datetime.now(timezone)):
                    continue
            except ExecutionLeaseLost:
                raise
            except Exception:
                logger.exception("email queue processing failed")
            finally:
                self._checkpoint("email", "idle")
            self.stop_event.wait(self.settings.worker_poll_seconds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--health',action='store_true')
    args = parser.parse_args()
    if args.health: return role_health(settings,'automation')
    logging.basicConfig(level=logging.INFO)
    process = AutomationProcess(settings)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum,lambda *_: process.stop_event.set())
    process.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
