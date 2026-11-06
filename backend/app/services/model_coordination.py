from contextlib import contextmanager
from time import monotonic, sleep, time
from uuid import uuid4

from app.database.coordination import CoordinationRepository
from app.database.postgres import PostgresDatabase
from app.core.process_lock import FileLock
from app.core.report_deadline import ensure_time_remaining
from app.core.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations


class ModelSlot:
    """OS-held permits cannot expire while a live caller still owns a model call."""
    def __init__(self, settings, database: PostgresDatabase, *, background, promoted, budget):
        self.settings = settings
        self.background = background
        self.promoted = promoted
        self.deadline = monotonic() + budget
        self.waiter = uuid4().hex
        self.repository = CoordinationRepository(database)
        self.locks = []
        self.directory = settings.data_dir / ".coordination"
        self.expires = time() + budget

    def register(self):
        if not self.background:
            self.repository.register_model_waiter(self.waiter, self.expires)

    def acquire(self):
        from app.services.llm_service import AnalysisCapacityError
        while True:
            promoted = self.promoted() if callable(self.promoted) else self.promoted
            ensure_time_remaining()
            if sql_request_cancellations.is_cancelled(current_request_id.get()):
                raise SqlRequestCancelled("智能分析请求已取消")
            if monotonic() >= self.deadline:
                raise AnalysisCapacityError("智能分析排队超时，请稍后重试。")
            foreground_waiting = self.repository.model_foreground_waiting()
            if not self.background or not foreground_waiting:
                background_lock = None
                if self.background and not promoted:
                    background_lock = self._take("background", max(1, self.settings.llm_max_concurrent // 2))
                if not self.background or promoted or background_lock:
                    lock = self._take("model", self.settings.llm_max_concurrent)
                    if lock:
                        self.locks = [item for item in (lock, background_lock) if item]
                        self.unregister()
                        return
                    if background_lock:
                        background_lock.release()
            sleep(min(0.05, max(0, self.deadline - monotonic())))

    def _take(self, prefix, limit):
        for index in range(limit):
            lock = FileLock(self.directory / f"{prefix}-{index}.lock")
            if lock.acquire(blocking=False):
                return lock
        return None

    def unregister(self):
        if not self.background:
            self.repository.unregister_model_waiter(self.waiter)

    def close(self):
        for lock in self.locks:
            lock.release()
        self.locks.clear()
        self.unregister()


@contextmanager
def model_slot(settings, database: PostgresDatabase, *, background, promoted, budget):
    slot = ModelSlot(settings, database, background=background, promoted=promoted, budget=budget)
    try:
        slot.register()
        yield slot
    finally:
        slot.close()
