"""Explicit PostgreSQL schema/status and stopped-Consumer maintenance."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.config import settings
from app.database.postgres import PostgresDatabase
from app.database.postgres_migrations import apply_migrations, prepare_consumers, validate_schema, MigrationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('schema', 'status', 'prepare-consumers'))
    args = parser.parse_args(argv)
    try:
        if args.action != 'status' and settings.postgres_user != 'cutting_migrator':
            raise MigrationError('维护必须显式使用cutting_migrator，不能使用正式运行账号')
        with PostgresDatabase(settings) as database:
            if args.action == 'prepare-consumers':
                print(json.dumps({'consumer_cutover': prepare_consumers(database)}, ensure_ascii=False))
            else:
                if args.action == 'schema':
                    result = apply_migrations(database)
                    with database.connect() as connection:
                        grants = (ROOT / 'scripts/postgres/grant_runtime.sql').read_text()
                        connection.execute(grants.replace('BEGIN;', '').replace('COMMIT;', ''), prepare=False)
                else:
                    with database.connect() as connection:
                        result = validate_schema(connection)
                print(json.dumps({'postgres_versions': result}, ensure_ascii=False, indent=2))
        return 0
    except MigrationError as exc:
        print(f'维护拒绝：{exc}', file=sys.stderr)
    except Exception as exc:
        # Driver messages may contain host, SQL values or credentials.
        print(f'维护失败（{type(exc).__name__}）；未标记成功，请核对连接、权限及备份。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
