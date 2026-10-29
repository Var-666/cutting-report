"""Inspect or upgrade an explicitly selected local application database."""
import argparse
from contextlib import ExitStack
from time import time
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.config import DEVICE_CATALOG
from app.database.coordination import backup_before_process_upgrade
from app.database.device_migrations import migrate_device_isolation
from app.database.migrations import apply_migrations
from app.database.migrations.registry import MIGRATIONS
from app.database.sqlite import SQLiteDatabase
from app.services.process_lock import FileLock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'upgrade'))
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    path = args.database.resolve()
    if args.action == 'upgrade':
        with ExitStack() as stack:
            web = FileLock(path.parent / '.web-process.lock')
            if not web.acquire(blocking=False):
                raise RuntimeError('请先停止 Web，再执行数据库升级')
            stack.callback(web.release)
            if path.exists():
                with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
                    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='process_leases'").fetchone():
                        if connection.execute("SELECT 1 FROM process_leases WHERE name='worker' AND expires_at>?", (time(),)).fetchone():
                            raise RuntimeError('请先停止 Worker 并等待有效租约释放，再执行升级')
            stack.enter_context(FileLock(path.with_suffix('.initialize.lock')))
            database = SQLiteDatabase(path)
            backup_before_process_upgrade(database)
            migrate_device_isolation(database, DEVICE_CATALOG)
            apply_migrations(database)
    applied = set()
    if path.exists():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
                applied = {row[0] for row in connection.execute('SELECT name FROM schema_migrations')}
    for name, _ in MIGRATIONS:
        print(f'{"applied" if name in applied else "pending"} {name}')


if __name__ == '__main__':
    main()
