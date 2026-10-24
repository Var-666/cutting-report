from __future__ import annotations

import logging
import threading
from concurrent.futures import wait
from typing import Callable

from app.services.llm_service import background_llm_work, generation_priority
from app.workers.bounded_executor import BoundedExecutor


logger = logging.getLogger(__name__)
_executor = BoundedExecutor(10, 32, "periodic-analysis")


class ParallelAnalysisTasks:
    """Share fixed execution capacity across devices and overlapping rounds."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stopped = False
        self._futures = set()

    def start(self, handler: Callable, *args) -> None:
        with self._lock:
            if self._stopped:
                return
            future = _executor.submit(self._run, handler, args)
            self._futures.add(future)
        future.add_done_callback(self._finished)

    def _finished(self, future):
        with self._lock:
            self._futures.discard(future)

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

    def stop(self, timeout: float = 5) -> None:
        with self._lock:
            self._stopped = True
            futures = tuple(self._futures)
        wait(futures, timeout=timeout)
