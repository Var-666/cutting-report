"""Invalidate local performance caches after another process refreshes data."""
import hashlib
import os
from uuid import uuid4


class CacheRevision:
    def __init__(self, settings, kind: str) -> None:
        identity = hashlib.sha256(settings.device_id.encode()).hexdigest()[:24]
        self.path = settings.data_dir / ".coordination" / f"cache-{identity}-{kind}"

    def version(self) -> tuple | None:
        try:
            stat = self.path.stat()
            return stat.st_ino, stat.st_mtime_ns
        except FileNotFoundError:
            return None

    def publish(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}-{uuid4().hex}.tmp")
        try:
            temporary.write_text(uuid4().hex, encoding="ascii")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
