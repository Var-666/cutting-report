from concurrent.futures import Future, wait
from contextvars import copy_context
from queue import Queue
from threading import BoundedSemaphore, Lock, Thread


class ExecutionQueueFull(RuntimeError):
    pass


class BoundedExecutor:
    """Fixed daemon consumers with bounded admission, including queued work."""
    def __init__(self, workers: int, queue_capacity: int, name: str) -> None:
        self._slots = BoundedSemaphore(workers + queue_capacity)
        self._queue = Queue(maxsize=workers + queue_capacity)
        self._lock = Lock()
        self._stopped = False
        self._workers = workers
        self._name = name
        self.threads = []
        self._futures = set()

    def submit(self, function, *args) -> Future:
        with self._lock:
            if self._stopped or not self._slots.acquire(blocking=False):
                raise ExecutionQueueFull(f"{self._name}执行容量已满")
            try:
                while len(self.threads) < self._workers:
                    index = len(self.threads)
                    thread = Thread(target=self._consume, name=f"{self._name}-{index}", daemon=True)
                    thread.start()
                    self.threads.append(thread)
            except BaseException:
                self._slots.release()
                raise
            future = Future()
            self._futures.add(future)
            self._queue.put_nowait((future, copy_context(), function, args))
            return future

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            future, context, function, args = item
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(context.run(function, *args))
                    except BaseException as exc:
                        future.set_exception(exc)
            finally:
                with self._lock:
                    self._futures.discard(future)
                self._slots.release()
                self._queue.task_done()

    def stop(self, timeout: float = 5) -> bool:
        with self._lock:
            self._stopped = True
            futures = tuple(self._futures)
        _, pending = wait(futures, timeout=timeout)
        if not pending:
            for _ in self.threads:
                self._queue.put(None)
        return not pending

    def available(self) -> bool:
        acquired = self._slots.acquire(blocking=False)
        if acquired:
            self._slots.release()
        return acquired and not self._stopped
