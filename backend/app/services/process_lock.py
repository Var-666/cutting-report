"""Compatibility imports; the shared implementation lives in app.core."""
from app.core.process_lock import (
    FileLock as FileLock,
    settings_file_lock as settings_file_lock,
)
