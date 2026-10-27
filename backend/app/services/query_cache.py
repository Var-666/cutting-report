"""Shared bounded L1 cache and in-process query coalescing."""
from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import Generic, TypeVar


_CachedResult = TypeVar("_CachedResult")


class QueryCache(Generic[_CachedResult]):
    """Bounded query cache; source reads never hold the publication lock."""

    def __init__(self, *, limit: int = 128) -> None:
        self._limit = limit
        self._entries: dict[tuple, tuple[float, float, _CachedResult]] = {}
        self._lock = Lock()
        self._query_locks = [Lock() for _ in range(32)]

    def read(
        self, key: tuple, loader: Callable[[], _CachedResult], *, ttl: int, force: bool = False
    ) -> _CachedResult:
        if ttl <= 0:
            return loader()
        requested_at = monotonic()
        with self._lock:
            cached = self._entries.get(key)
            if not force and cached and cached[0] > requested_at:
                return cached[2]
        query_lock = self._query_locks[hash(key) % len(self._query_locks)]
        with query_lock:
            now = monotonic()
            with self._lock:
                cached = self._entries.get(key)
                cache_is_fresh = cached and cached[0] > now
                refreshed_after_request = cached and cached[1] >= requested_at
                if (not force and cache_is_fresh) or refreshed_after_request:
                    return cached[2]
            result = loader()
            created_at = monotonic()
            with self._lock:
                self._entries = {
                    cache_key: value for cache_key, value in self._entries.items()
                    if value[0] > created_at
                }
                self._entries[key] = (created_at + ttl, created_at, result)
                while len(self._entries) > self._limit:
                    self._entries.pop(next(iter(self._entries)))
            return result
