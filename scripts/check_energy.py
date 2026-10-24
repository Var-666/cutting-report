"""Run on an intranet host: python scripts/check_energy.py --device-id Cutter01 --date 2026-08-31.

Read-only diagnostic. Password is read from local environment or a hidden prompt;
neither it nor CompanyId/AuthId is printed or saved. No production writes.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from getpass import getpass
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "backend"))

from app.config import DEVICE_CATALOG, settings, settings_for_device
from app.models.domain import to_primitive
from app.services.energy_api import EnergyApiClient, EnergyApiError
from app.services.energy_service import make_series


def main():
    parser = argparse.ArgumentParser(description="只读核对能管四个累计量参数，结果不代表全天用量。")
    parser.add_argument("--device-id", required=True, choices=[item.id for item in DEVICE_CATALOG.devices])
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", type=Path, help="可选：导出原始采集 JSON（不含授权信息），供内网联调核对")
    args = parser.parse_args()
    device_settings = settings_for_device(settings, args.device_id)
    if not device_settings.energy_connected:
        parser.error("所选设备能耗暂未接入")
    parameters = {"累计流量": device_settings.energy_gas_parameter_id,
                  **{f"电表 {index} 正向有功电能": key for index, key in enumerate(device_settings.energy_report_electricity_parameter_ids, 1)}}
    cfg = replace(device_settings,
        energy_api_username=settings.energy_api_username or input("能管账号: ").strip(),
        energy_api_password=settings.energy_api_password or getpass("能管密码（隐藏输入）: "))
    start = datetime.combine(args.date, time.min)
    end = start + timedelta(days=1)
    try:
        rows = EnergyApiClient(cfg).fetch(tuple(parameters.values()), start, end, force=True)
        series = make_series(rows, parameters.values(), cfg.timezone)
    except EnergyApiError as exc:
        print(f"查询未成功：{exc}")
        return 1
    print(f"登录与查询成功；查询日期：{args.date}（{cfg.timezone}）；返回 {len(rows)} 条记录。")
    for label, key in parameters.items():
        points, invalid = series[key]
        print(f"{label}: {len(points)} 个采样点" + ("，存在无法解析的时间" if invalid else ""))
        if points:
            print(f"  首条 {points[0][0]} = {points[0][1]}；末条 {points[-1][0]} = {points[-1][1]}")
    print("以上是累计读数，不是日电耗/气耗；必须按任务实际起止时刻匹配，且确认电表边界和单位。")
    if args.output:
        # Explicit optional output contains only readings, never login response.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(to_primitive(rows), stream, ensure_ascii=False, indent=2)
        print("原始采集值已导出至指定文件（不含账号或授权码）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
