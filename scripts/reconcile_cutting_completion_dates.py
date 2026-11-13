"""Preview/apply historical workshop dates using read-only source END_TIME.

Back up PostgreSQL and stop application roles before --apply. No source rows are written.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.config import settings
from app.database.cutting_queries import CuttingQueryRepository
from app.database.postgres import PostgresDatabase
from app.database.source_registry import DatabaseSourceRegistry
from app.database.workshop_reports import WorkshopReportRepository
from app.database.workshop_errors import DailyReportRecordMismatchError, DailyReportSourceMismatchError
from app.device_config import load_line_catalog
from app.services.cache_revision import CacheRevision


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--line', required=True, help='已登记的切割产线ID，如 Cutter01')
    parser.add_argument('--apply', action='store_true', help='提交校正；默认仅预览')
    args = parser.parse_args(argv)
    sources = None
    try:
        line = next((line for line in load_line_catalog(settings.production_lines_json).lines if line.id == args.line), None)
        if line is None or line.type != 'cutting':
            raise ValueError('必须指定已登记的切割产线')
        sources = DatabaseSourceRegistry.from_json(settings.database_sources_json)
        if sources.get(line.source_key).engine != 'sqlserver':
            raise ValueError('切割产线必须绑定 SQL Server')
        source = CuttingQueryRepository(sources.client(line.source_key), line.machine_no)
        with PostgresDatabase(settings) as database:
            database.check_runtime()
            reports = WorkshopReportRepository(database, scope_machine_no=line.machine_no)
            ids = reports.completion_date_task_ids()
            dates = source.task_completion_dates(ids)
            result = reports.reconcile_completion_dates(dates, apply=args.apply)
            unresolved = sorted(set(ids) - dates.keys())
            if args.apply:
                cfg = replace(settings, device_id=line.id)
                CacheRevision(cfg, 'production').publish()
                CacheRevision(cfg, 'leadership').publish()
            print(json.dumps(dict(line=line.id, applied=args.apply, scanned_tasks=len(ids), **result,
                unresolved_tasks=len(unresolved), unresolved_sample=unresolved[:20]), ensure_ascii=False, indent=2))
            if unresolved:
                print('部分任务已不存在、未完成或缺少有效结束时间，已保留原快照，请现场核对。', file=sys.stderr)
                return 2
        return 0
    except (DailyReportRecordMismatchError, DailyReportSourceMismatchError) as exc:
        print(f'校正拒绝：{exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        # Database errors can contain connection details and source values.
        print(f'校正未完成（{type(exc).__name__}）；请重新预览并核对来源、权限及备份。', file=sys.stderr)
        return 1
    finally:
        if sources is not None:
            sources.close()


if __name__ == '__main__':
    raise SystemExit(main())
