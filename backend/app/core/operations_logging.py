"""Process-local structured logging with bounded, isolated shared-volume files."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import stat
import sys
from threading import Event, Thread
from time import monotonic, time
from uuid import uuid4

from app.core.process_lock import FileLock

ROLES = ('web', 'scheduler', 'worker', 'automation')
IDENTIFIER = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}')
PUBLIC_REQUEST_ID = re.compile(
    r'req_[0-9a-f]{12}|ref_[0-9a-f]{24}|web_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|web_[a-z0-9]{7,10}_[a-z0-9]{6,16}'
)
FILE_NAME = re.compile(r'(web|scheduler|worker|automation)-[0-9a-f]{24}\.jsonl(?:\.[1-3])?')
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_INSTANCES = 32
FIELDS = ('request_id', 'analysis_id', 'task_id', 'report_id', 'line_id', 'source_id',
          'stage', 'error_code', 'resource_id', 'related_kind', 'run_id', 'error_type')
QUALITY_FIELDS = ('state', 'period_start', 'period_end', 'data_as_of', 'missing_count', 'estimated_count',
                  'data_version', 'summary_count', 'daily_count', 'grouped_count')
QUALITY_VALUE = re.compile(r'[A-Za-z0-9_.:+-]{1,160}')
_context: ContextVar[dict] = ContextVar('operations_log_context', default={})


@contextmanager
def operation_context(**fields):
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def event(logger, level, message, *, error_code=None, incident=False, exc_info=False, **fields):
    """Message is a fixed source-code literal. Values go in allowlisted fields."""
    logger.log(level, message, exc_info=exc_info, extra={
        'operations_message': message, 'operations_incident': incident,
        'operations_fields': {**fields, 'error_code': error_code},
    }, stacklevel=2)


def data_quality(logger, code, message, *, scope, level, state, line_id, source_id, stage, analysis_id=None, **details):
    """Observe an actual data result, or record recovery after a successful validation."""
    resource = sha256(json.dumps(scope, separators=(',', ':'), sort_keys=True).encode()).hexdigest()
    logger.log(level, message, extra={
        'operations_message': message,
        'operations_quality': {'state': state, **details},
        'operations_fields': {'error_code': code, 'line_id': line_id, 'source_id': source_id,
                              'resource_id': resource, 'stage': stage, 'analysis_id': analysis_id},
    }, stacklevel=2)


def safe_id(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def log_request_id(value):
    identifier = safe_id(value)
    if identifier and not PUBLIC_REQUEST_ID.fullmatch(identifier):
        return 'ref_' + sha256(identifier.encode()).hexdigest()[:24]
    return identifier


def safe_record(record, role, instance):
    fields = {**_context.get(), **getattr(record, 'operations_fields', {})}
    for name in FIELDS:
        if name in record.__dict__:
            fields.setdefault(name, record.__dict__[name])
    captured = {name: value for name in FIELDS if (value := safe_id(fields.get(name))) is not None}
    if 'request_id' in captured:
        captured['request_id'] = log_request_id(captured['request_id'])
    if type(fields.get('duration_ms')) in (int, float) and 0 <= fields['duration_ms'] < 1e10:
        captured['duration_ms'] = round(fields['duration_ms'], 1)
    if type(fields.get('attempt')) is int and 0 <= fields['attempt'] <= 1000000:
        captured['attempt'] = fields['attempt']
    if (quality := getattr(record, 'operations_quality', None)) is not None:
        captured['data_quality'] = {key: str(value) for key in QUALITY_FIELDS
                                    if (value := quality.get(key)) is not None and QUALITY_VALUE.fullmatch(str(value))}
    source = safe_id(record.name) or 'logging'
    component = source.removeprefix('app.')
    message = getattr(record, 'operations_message', None)
    item = {
        'id': uuid4().hex,
        'timestamp': datetime.fromtimestamp(record.created, UTC).isoformat(timespec='microseconds'),
        'level': record.levelname if record.levelname in {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'} else 'INFO',
        'role': role, 'instance_id': instance, 'logger': source, 'component': component,
        'message': message or f'{component}.{safe_id(record.funcName) or "runtime"}:{record.lineno}',
        **captured,
    }
    if record.exc_info and record.exc_info[0]:
        kind, _, trace = record.exc_info
        frames = deque(maxlen=12)
        while trace:
            code = trace.tb_frame.f_code
            frames.append({'file': Path(code.co_filename).name, 'function': code.co_name, 'line': trace.tb_lineno})
            trace = trace.tb_next
        item['exception'] = {'type': kind.__name__, 'frames': list(frames)}
    return item


def log_root(logs_dir: Path) -> Path:
    return logs_dir / 'operations'


def log_instance_suffix(instance: str) -> str:
    return sha256(instance.encode()).hexdigest()[:24]


def log_paths(root: Path):
    if root.is_symlink():
        raise OSError('invalid operations log directory')
    with os.scandir(root) as entries:
        for entry in entries:
            if FILE_NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                yield Path(entry.path)


def prune_logs(logs_dir: Path, days: int) -> None:
    root = log_root(logs_dir)
    if not root.is_dir():
        return
    with FileLock(root / '.directory.lock'):
        _prune_inactive_segments(root, time() - days * 86400)


def _prune_inactive_segments(root: Path, cutoff: float) -> None:
    """Caller holds the directory lock so no new writer can enter during cleanup."""
    locks = {}
    try:
        for path in log_paths(root):
            base = path.name.partition('.jsonl')[0]
            if base not in locks:
                lock = FileLock(root / (base + '.lock'))
                locks[base] = lock if lock.acquire(blocking=False) else None
            if locks[base] and path.stat(follow_symlinks=False).st_mtime < cutoff:
                path.unlink(missing_ok=True)
    finally:
        for lock in locks.values():
            if lock:
                lock.release()


class PrivateRotatingFile(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError('invalid operations log file')
        return os.fdopen(fd, 'a', encoding='utf-8')

    def handleError(self, record):
        if not getattr(self, 'failed', False):
            self.failed = True
            print('operations: rolling log file write failed; see stdout', file=sys.stderr)


class OperationsLogging(logging.Handler):
    def __init__(self, logs_dir: Path, role: str, instance: str, retention_days: int):
        super().__init__()
        self.role = role
        self.retention_days = retention_days
        self.instance = safe_id(instance) or role
        self.items: Queue = Queue(1024)
        self.incidents: Queue = Queue(256)
        self.done = Event()
        self.dropped = 0
        self.incident_dropped = 0
        self.file = None
        self.lock_file = None
        self._open_file(log_root(logs_dir))
        self.thread = Thread(target=self._write, name='operations-log', daemon=True)
        self.previous = {}

    def _open_file(self, root):
        base = f'{self.role}-{log_instance_suffix(self.instance)}'
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.is_symlink():
                raise OSError('invalid operations log directory')
            with FileLock(root / '.directory.lock'):
                bases = {path.name.partition('.jsonl')[0] for path in log_paths(root)}
                if base not in bases and len(bases) >= MAX_INSTANCES:
                    _prune_inactive_segments(root, time() - self.retention_days * 86400)
                    remaining = {path.name.partition('.jsonl')[0] for path in log_paths(root)}
                    if len(remaining) >= MAX_INSTANCES:
                        raise OSError('operations log instance limit')
                lock = FileLock(root / (base + '.lock'))
                if not lock.acquire(blocking=False):
                    raise OSError('operations log instance already active')
                self.lock_file = lock
                self.file = PrivateRotatingFile(root / (base + '.jsonl'), maxBytes=MAX_FILE_BYTES,
                                                backupCount=3, encoding='utf-8')
                self.file.setFormatter(logging.Formatter('%(message)s'))
        except OSError:
            if self.lock_file:
                self.lock_file.release()
                self.lock_file = None
            print(f'operations: {self.role} log file unavailable; see stdout', file=sys.stderr)

    def emit(self, record):
        if self.done.is_set() or record.name == 'uvicorn.access':
            return
        if record.levelno < logging.WARNING and not getattr(record, 'operations_message', None):
            return
        item = safe_record(record, self.role, self.instance)
        try:
            self.items.put_nowait(item)
        except Full:
            self.dropped += 1
        quality = 'data_quality' in item
        if item.get('error_code') and (quality or getattr(record, 'operations_incident', False)):
            try:
                self.incidents.put_nowait({**item, 'quality_observation': True} if quality else item)
            except Full:
                self.incident_dropped += 1

    def _write(self):
        next_prune = 0.0
        try:
            while not self.done.is_set() or not self.items.empty():
                if self.file and monotonic() >= next_prune:
                    next_prune = monotonic() + 3600
                    cutoff = time() - self.retention_days * 86400
                    for index in range(self.file.backupCount + 1):
                        segment = Path(f'{self.file.baseFilename}.{index}' if index else self.file.baseFilename)
                        try:
                            if segment.stat(follow_symlinks=False).st_mtime < cutoff:
                                if index:
                                    segment.unlink()
                                elif self.file.stream is not None:
                                    # Keep the current path reserved for this live writer.
                                    self.file.stream.truncate(0)
                        except FileNotFoundError:
                            pass
                        except OSError:
                            print('operations: expired segment cleanup failed', file=sys.stderr)
                try:
                    item = self.items.get(timeout=0.2)
                except Empty:
                    continue
                payload = json.dumps(item, ensure_ascii=True, separators=(',', ':'))
                try:
                    print(payload, file=sys.stdout, flush=True)
                except (OSError, ValueError):
                    pass
                if self.file:
                    self.file.emit(logging.LogRecord('operations', logging.INFO, '', 0, payload, (), None))
        finally:
            if self.file:
                self.file.close()
            if self.lock_file:
                self.lock_file.release()

    def start(self):
        for name in ('', 'uvicorn', 'uvicorn.error', 'uvicorn.access'):
            logger = logging.getLogger(name)
            previous = logger.handlers[:], logger.level, logger.propagate
            self.previous[name] = previous
            logger.handlers = [self] if not name else []
            logger.setLevel(logging.INFO)
            logger.propagate = bool(name)
        self.thread.start()
        return self

    def stop(self):
        for name, (handlers, level, propagate) in self.previous.items():
            logger = logging.getLogger(name)
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
        self.done.set()
        self.thread.join(3)
        if self.dropped or self.incident_dropped:
            print(f'operations: dropped logs={self.dropped} incident samples={self.incident_dropped}', file=sys.stderr)


@contextmanager
def configured_logging(logs_dir, role, instance, retention_days=14):
    logs = OperationsLogging(logs_dir, role, instance, retention_days).start()
    logger = logging.getLogger('app.lifecycle')
    event(logger, logging.INFO, '应用进程启动')
    try:
        yield logs
    except Exception:
        event(logger, logging.CRITICAL, '应用进程异常退出', error_code='PROCESS_EXIT', exc_info=True)
        # Uvicorn and Python may print the exception after these handlers have stopped.
        raise RuntimeError('应用进程异常退出，请查看 PROCESS_EXIT 运维日志') from None
    finally:
        event(logger, logging.INFO, '应用进程退出')
        logs.stop()
