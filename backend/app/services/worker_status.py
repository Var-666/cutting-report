from threading import Lock
from time import monotonic

from app.database.coordination import CoordinationRepository
from app.database.sqlite import SQLiteDatabase


def background_ready(status: dict) -> bool:
    return bool(status.get("report_alive") and status.get("automation_alive") is not False
                and status.get("email_alive") is not False and not status.get("stalled_workers")
                and status.get("queue", {}).get("available") is not False)


class WorkerStatusReader:
    def __init__(self, path) -> None:
        self.repository = CoordinationRepository(SQLiteDatabase(path, timeout_seconds=1))
        self._lock = Lock()
        self._next_read = 0.0
        self._cached = {"state": "missing", "report_alive": False}

    def status(self) -> dict:
        with self._lock:
            if monotonic() >= self._next_read:
                try:
                    self._cached = {**self.repository.worker_status(), "database_available": True}
                except Exception:
                    self._cached = {"state": "unavailable", "report_alive": False, "database_available": False}
                self._next_read = monotonic() + 2
            return dict(self._cached)
