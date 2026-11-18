"""Merge fixed, private log segments with explicit scan and retention limits."""
from datetime import UTC, datetime, timedelta
import heapq
import json
import os
import stat

from app.core.operations_logging import MAX_FILE_BYTES, MAX_INSTANCES, log_instance_suffix, log_paths, log_root
from app.models.operations import LogRecord

MAX_SCAN_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 8192


class OperationsLogQuery:
    def __init__(self, logs_dir, retention_days: int):
        self.root = log_root(logs_dir)
        self.retention_days = retention_days

    def query(self, filters, *, totals=False):
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        issues = set()
        if filters.start < cutoff:
            issues.add('retention')
        if filters.cursor:
            instant, _, identifier = filters.cursor.partition('/')
            boundary = (datetime.fromisoformat(instant), identifier)
        else:
            boundary = (filters.end, '\uffff')
        results = []
        errors = warnings = 0
        budget = MAX_SCAN_BYTES
        earliest = None
        latest = None
        by_instance = {}
        full_rotation = set()
        role = filters.role
        suffix = log_instance_suffix(filters.instance_id) if filters.instance_id else None
        entries = []
        try:
            for path in log_paths(self.root):
                if role and not path.name.startswith(role + '-'):
                    continue
                if suffix and not path.name.partition('.jsonl')[0].endswith('-' + suffix):
                    continue
                try:
                    entries.append((path.stat(follow_symlinks=False).st_mtime, path))
                except FileNotFoundError:
                    issues.add('rotation')
                except OSError:
                    issues.add('segment')
        except OSError:
            issues.add('unavailable')
        # Modification time prioritizes likely matches without assuming file order equals event order.
        target = boundary[0].timestamp()
        entries.sort(key=lambda entry: (abs(entry[0] - target), -entry[0], entry[1]))
        for modified, path in entries[:MAX_INSTANCES * 4]:
            base = path.name.partition('.jsonl')[0]
            if path.name.endswith('.3'):
                full_rotation.add(base)
            if modified < max(cutoff, filters.start).timestamp():
                continue
            try:
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
                    metadata = os.fstat(stream.fileno())
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
                        issues.add('segment')
                        continue
                    if metadata.st_size > budget:
                        issues.add('scan_limit')
                        continue
                    budget -= metadata.st_size
                    data = stream.read(metadata.st_size)
                if len(data) != metadata.st_size or path.stat(follow_symlinks=False).st_ino != metadata.st_ino:
                    issues.add('rotation')
                for raw in data.splitlines():
                    if len(raw) > MAX_LINE_BYTES:
                        issues.add('segment')
                        continue
                    try:
                        item = LogRecord.model_validate(json.loads(raw))
                    except (ValueError, TypeError):
                        issues.add('segment')
                        continue
                    if item.timestamp < cutoff:
                        continue
                    previous = by_instance.get(base)
                    by_instance[base] = item.timestamp if previous is None else min(previous, item.timestamp)
                    earliest = item.timestamp if earliest is None else min(earliest, item.timestamp)
                    latest = item.timestamp if latest is None else max(latest, item.timestamp)
                    key = (item.timestamp, item.id)
                    if item.timestamp < filters.start or key >= boundary or not filters.matches(item):
                        continue
                    if totals:
                        errors += item.level in ('ERROR', 'CRITICAL')
                        warnings += item.level == 'WARNING'
                        continue
                    entry = (*key, raw)
                    if len(results) < filters.limit + 1:
                        heapq.heappush(results, entry)
                    else:
                        heapq.heappushpop(results, entry)
            except OSError:
                issues.add('rotation')
        if len(entries) > MAX_INSTANCES * 4:
            issues.add('scan_limit')
        # A rolling stream can lose older records even within the configured age.
        if earliest is None or filters.start < earliest or any(
                base not in by_instance or filters.start < by_instance[base] for base in full_rotation):
            issues.add('coverage')
        if totals:
            if earliest is None:
                return None
            return dict(errors=errors, warnings=warnings, complete=not issues)
        rows = [json.loads(raw) for _, _, raw in sorted(results, reverse=True)[:filters.limit]]
        cursor = f'{rows[-1]["timestamp"]}/{rows[-1]["id"]}' if len(results) > filters.limit else None
        return dict(items=rows, next_cursor=cursor, coverage_warnings=sorted(issues),
                    earliest_scanned_at=earliest, latest_scanned_at=latest)
