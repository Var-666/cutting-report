"""Read-only source audit and day/week/month timing, using the saved backend config."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app.config import Settings
from app.database.welding import WeldingClient, WeldingRepository, WeldingUnavailable, TABLES, valid_serial, valid_time
from app.models.domain import to_primitive
from app.models.welding import WeldingQuery
from app.services.welding_service import WeldingService


def inspect_source(client: WeldingClient) -> dict:
    from mysql.connector import Error
    result = {'source': '小组立焊接线', 'tables': {}}
    with client.snapshot() as session:
        grants = session.fetch('SHOW GRANTS FOR CURRENT_USER()')
        statements = [str(next(iter(row.values()))) for row in grants]
        result['account_requires_grant_review'] = any(not text.startswith(('GRANT SELECT ON ', 'GRANT USAGE ON ')) for text in statements)
        for table in (*TABLES, 'weld_plan_info_table', 'production_stats'):
            try:
                columns = session.fetch(f'SHOW COLUMNS FROM {table}', limit=101)
                indexes = session.fetch(f'SHOW INDEX FROM {table}', limit=1001)
                total = session.fetch(f'SELECT COUNT(*) AS total, MAX(update_date) AS updated_at FROM {table}', limit=1)[0]
            except Error as exc:
                if table in TABLES:
                    raise
                result['tables'][table] = {'inspection': 'optional_table_unavailable', 'error_code': exc.errno}
                continue
            info = {'columns': columns, 'indexes': indexes, **total}
            if table in TABLES:
                info['state_distribution'] = session.fetch(f'SELECT state, COUNT(*) AS total FROM {table} GROUP BY state ORDER BY state', limit=1001)
                info['invalid_serial_rows'] = session.fetch(f'SELECT COUNT(*) AS total FROM {table} WHERE NOT COALESCE({valid_serial("serial_number")}, 0)', limit=1)[0]['total']
                info['unassignable_end_time_rows'] = session.fetch(f'SELECT COUNT(*) AS total FROM {table} WHERE NOT COALESCE({valid_time("end_time")}, 0)', limit=1)[0]['total']
            result['tables'][table] = info
        result['duplicate_part_serial_groups'] = session.fetch(f'SELECT COUNT(*) AS total FROM (SELECT serial_number FROM weld_part_info_table WHERE {valid_serial("serial_number")} GROUP BY serial_number HAVING COUNT(*) > 1) duplicates', limit=1)[0]['total']
    return to_primitive(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end', type=date.fromisoformat, required=True, help='typical production date YYYY-MM-DD')
    parser.add_argument('--runs', type=int, choices=range(1, 11), default=3)
    args = parser.parse_args()
    cfg = Settings()
    client = WeldingClient(cfg)
    service = WeldingService(cfg, WeldingRepository(client))
    try:
        report = inspect_source(client)
        report['verified_rules'] = {'length_input': 'mm', 'length_output': 'm', 'timezone': cfg.weld_db_timezone,
                                    'production_date': 'end_time', 'duration': 'used_time', 'completed_state': 'pending'}
        report['timing'] = []
        for label, start in [('day', args.end), ('week', args.end - timedelta(days=6)), ('month', args.end.replace(day=1))]:
            query = WeldingQuery(start=start, end=args.end)
            for kind in ('daily', 'jobs', 'parts', 'equipment'):
                durations = []
                sample = None
                for _ in range(args.runs):
                    started = perf_counter()
                    sample = service.report(kind, query)
                    durations.append((perf_counter() - started) * 1000)
                report['timing'].append({'range': label, 'report': kind, 'sample_count': len(durations),
                    'first_ms': round(durations[0]), 'p50_service_ms': round(statistics.median(durations)),
                    'p95_service_ms': round(sorted(durations)[max(0, int(len(durations) * .95 + .999) - 1)]),
                    'total': sample['total'], 'quality': sample['meta']['quality']})
        report['limitations'] = ['服务调用耗时不是HTTP端到端延迟；低样本P95仅用于初查。',
                                 '索引只检查不修改；状态、机器人映射、包角关系需业务确认。',
                                 '不输出账号、授权原文、密码或连接地址；不写源库。']
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except WeldingUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
