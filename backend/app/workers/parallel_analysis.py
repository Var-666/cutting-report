from __future__ import annotations

import logging
import threading
from time import monotonic
from typing import Callable

from app.services.llm_service import background_llm_work, generation_priority


logger = logging.getLogger(__name__)


class ParallelAnalysisTasks:
    """Run each scheduled analysis independently, including overlapping rounds."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stopped = False
        self._threads: set[threading.Thread] = set()

    def start(self, handler: Callable, *args) -> None:
        with self._lock:
            if self._stopped:
                return
            thread = threading.Thread(
                target=self._run, args=(handler, args),
                name=f"periodic-analysis-{handler.__name__}", daemon=True,
            )
            self._threads.add(thread)
            try:
                thread.start()
            except Exception:
                self._threads.discard(thread)
                raise

    @background_llm_work()
    def _run(self, handler: Callable, args: tuple) -> None:
        try:
            # Fixed-cadence model calls may use the shared pool beyond its
            # opportunistic background quota, without raising the global limit.
            priority = threading.Event()
            priority.set()
            with generation_priority(priority):
                handler(*args)
        except Exception:
            logger.exception("periodic analysis failed: %s", handler.__name__)
        finally:
            with self._lock:
                self._threads.discard(threading.current_thread())

    def stop(self, timeout: float = 5) -> None:
        with self._lock:
            self._stopped = True
            threads = tuple(self._threads)
        deadline = monotonic() + timeout
        for thread in threads:
            thread.join(timeout=max(0, deadline - monotonic()))
