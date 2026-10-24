"""Inspect or reset the selected device monitoring window.

Run on the deployment host with the application stopped. Without --apply this
only reads the production alarm high-water mark and the application cursor.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "backend"))

from app.config import DEVICE_CATALOG, settings, settings_for_device
from app.database.alarm_source import SqlServerAlarmSource
from app.database.alarms import AlarmRepository
from app.database.device_migrations import migrate_device_isolation
from app.services.process_lock import FileLock


def main() -> int:
    parser = argparse.ArgumentParser(description="检查或重置指定设备告警统计起点")
    parser.add_argument("--device-id", required=True, choices=[item.id for item in DEVICE_CATALOG.devices])
    parser.add_argument("--apply", action="store_true", help="确认服务已停止后，重置游标并重新补录起始日数据")
    args = parser.parse_args()

    cfg = settings_for_device(settings, args.device_id)
    repository = AlarmRepository(cfg.metadata_db_path, machine_no=cfg.report_machine_no,
                                 source_name=f'{cfg.production_data_source}:{cfg.device_id}')
    source = SqlServerAlarmSource(cfg)
    current = (
        repository.get_sync_state()
        if settings.metadata_db_path.exists()
        else {"last_alarm_id": 0, "last_success_at": None}
    )
    latest = source.latest_id()
    print(
        f"设备 {cfg.device_id}；统计起点 {cfg.alarm_monitor_start_at}；"
        f"当前游标 {current['last_alarm_id']}；数据库最新编号 {latest}"
    )
    if not args.apply:
        print("只读预览；停服务后加 --apply 可从统计起点重新同步。")
        return 0

    with FileLock(cfg.metadata_db_path.with_suffix(".initialize.lock")):
        migrate_device_isolation(repository.database, DEVICE_CATALOG)
        repository.initialize()
    repository.configure_monitoring_window(
        cfg.alarm_monitor_start_at,
        force=True,
    )
    state = repository.get_sync_state()
    print(
        f"已将游标重置为 {state['last_alarm_id']}；启动服务后只补录 "
        f"{state['monitor_start_at']} 及之后的告警。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
