"""OS locks shared by the Web and Worker containers' local data volume."""
from contextlib import contextmanager
from pathlib import Path
from threading import local
import os


class FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self, *, blocking: bool = True) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except (BlockingIOError, PermissionError, OSError) as exc:
            handle.close()
            if not blocking and exc.errno in {11, 13, 35, 36}:
                return False
            raise
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


_held = local()


@contextmanager
def settings_file_lock(path: Path):
    """Allow nested reads/writes inside the same settings read-modify-write."""
    key = str(path.resolve())
    held = getattr(_held, "paths", None)
    if held is None:
        held = _held.paths = set()
    if key in held:
        yield
        return
    with FileLock(path.with_name(path.name + ".lock")):
        held.add(key)
        try:
            yield
        finally:
            held.remove(key)
