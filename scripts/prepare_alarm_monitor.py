"""Inspect or reset the selected device monitoring window.

Run on the deployment host with the application stopped. Without --apply this
only reads the production alarm high-water mark and the application cursor.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "backend"))

from app.config import settings, settings_for_line
from app.device_config import load_line_catalog
from app.database.source_registry import DatabaseSourceRegistry
from app.database.alarm_source import SqlServerAlarmSource
from app.database.alarms import AlarmRepository
from app.database.postgres import PostgresDatabase
from app.database.coordination import CoordinationRepository
from app.core.process_lock import FileLock
from app.core.sql_capacity import sql_identity


def main() -> int:
    parser = argparse.ArgumentParser(description="检查或重置指定设备告警统计起点")
    parser.add_argument("--device-id", required=True, choices=[item.id for item in load_line_catalog(settings.production_lines_json).lines if item.type == "cutting"])
    parser.add_argument("--apply", action="store_true", help="确认服务已停止后，重置游标并重新补录起始日数据")
    args = parser.parse_args()

    catalog = load_line_catalog(settings.production_lines_json)
    sources = DatabaseSourceRegistry.from_json(settings.database_sources_json)
    line = catalog.get(args.device_id)
    cfg = settings_for_line(settings, line, sources.fingerprint(line.source_key))
    cfg = replace(cfg, alarm_source_name=f'{line.id}:{sources.connection_identity(line.source_key)}')

    source = SqlServerAlarmSource(cfg, client=sources.client(cfg.production_data_source))
    with sql_identity(line.id):
        latest = source.latest_id()
    with PostgresDatabase(cfg) as database:
        database.check_runtime()
        repository = AlarmRepository(database, machine_no=cfg.report_machine_no, source_name=cfg.alarm_source_name)
        current = repository.get_sync_state()
        print(f"设备 {cfg.device_id}；统计起点 {cfg.alarm_monitor_start_at}；当前游标 {current['last_alarm_id']}；数据库最新编号 {latest}")
        if not args.apply:
            print("只读预览；停服务后加 --apply 可从统计起点重新同步。")
            return 0
        lock = FileLock(cfg.data_dir / '.web-process.lock')
        if not lock.acquire(blocking=False):
            raise RuntimeError('请先停止 Web，再重置告警统计起点')
        try:
            if CoordinationRepository(database).worker_status()['state'] in ('running', 'stalled'):
                raise RuntimeError('请先停止 Worker 并等待租约释放')
            repository.configure_monitoring_window(cfg.alarm_monitor_start_at, force=True)
        finally:
            lock.release()
        state = repository.get_sync_state()
        print(f"已将游标重置为 {state['last_alarm_id']}；启动服务后只补录 {state['monitor_start_at']} 及之后的告警。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
