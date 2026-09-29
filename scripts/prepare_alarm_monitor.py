"""Inspect or reset the configured Cutter01 monitoring window.

Run on the deployment host with the application stopped. Without --apply this
only reads the production alarm high-water mark and the application cursor.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "backend"))

from app.config import settings
from app.database.alarm_source import SqlServerAlarmSource
from app.database.alarms import AlarmRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="检查或重置 Cutter01 告警统计起点")
    parser.add_argument("--apply", action="store_true", help="确认服务已停止后，重置游标并重新补录起始日数据")
    args = parser.parse_args()

    if settings.alarm_monitor_machine_nos != ("Cutter01",):
        parser.error("生效的 ALARM_MONITOR_MACHINE_NOS 必须仅为 Cutter01")
    repository = AlarmRepository(settings.metadata_db_path)
    source = SqlServerAlarmSource(settings)
    current = (
        repository.get_sync_state()
        if settings.metadata_db_path.exists()
        else {"last_alarm_id": 0, "last_success_at": None}
    )
    latest = source.latest_id()
    print(
        f"设备 Cutter01；统计起点 {settings.alarm_monitor_start_at}；"
        f"当前游标 {current['last_alarm_id']}；数据库最新编号 {latest}"
    )
    if not args.apply:
        print("只读预览；停服务后加 --apply 可从统计起点重新同步。")
        return 0

    repository.initialize()
    repository.configure_monitoring_window(
        settings.alarm_monitor_start_at,
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
