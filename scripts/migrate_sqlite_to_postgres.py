"""P4 offline snapshot importer; never switches the Web/Worker runtime backend."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.config import settings
from app.database.postgres import PostgresDatabase
from app.database.postgres_migrations import apply_migrations, MigrationError
from app.database.sqlite_to_postgres import offline_snapshot, transfer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('schema', 'status', 'check', 'migrate', 'verify'))
    parser.add_argument('--sqlite', type=Path)
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--reports-root', type=Path, default=settings.reports_dir)
    parser.add_argument('--data-root', type=Path, default=settings.data_dir)
    parser.add_argument('--batch-size', type=int, default=1000)
    args = parser.parse_args(argv)
    if args.action not in ('schema', 'status') and not args.sqlite:
        parser.error('check/migrate/verify 必须指定 --sqlite；PostgreSQL 连接只取 POSTGRES_* 配置，不接收明文密码参数')
    try:
        with PostgresDatabase(settings) as database:
            if args.action in ('schema','status'):
                if args.action=='schema': result = apply_migrations(database)
                else:
                    from app.database.postgres_migrations import validate_schema
                    with database.connect() as connection: result = validate_schema(connection)
                print(json.dumps({'postgres_versions':result},ensure_ascii=False,indent=2))
                return 0
            with offline_snapshot(args.sqlite, args.backup_dir or args.sqlite.resolve().parent / 'backups') as (source,backup):
                if args.action in ('check','migrate'): apply_migrations(database)
                result = transfer(source,database,action=args.action,batch_size=args.batch_size,
                                  reports_root=args.reports_root,data_root=args.data_root)
                result['backup'] = str(backup)
                print(json.dumps(result,ensure_ascii=False,indent=2))
                return 0
    except MigrationError as exc:
        print(f'迁移拒绝：{exc}',file=sys.stderr)
    except Exception as exc:
        # Driver messages may contain host, SQL values or credentials.
        print(f'迁移失败（{type(exc).__name__}）；未标记成功，请核对连接、权限及备份。',file=sys.stderr)
    return 1


if __name__=='__main__':
    raise SystemExit(main())
