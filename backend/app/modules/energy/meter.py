from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo


def local_time(value, timezone: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(ZoneInfo(timezone)).replace(tzinfo=None) if parsed.tzinfo else parsed


def make_series(rows, ids, timezone):
    readings: dict[str, dict[datetime, Decimal | None]] = {key: {} for key in ids}
    invalid = set()
    for row in rows:
        key = str(row.get("MfId", "")).lower()
        if key not in readings:
            continue
        try:
            stamp = local_time(row.get("Mt"), timezone)
        except (TypeError, ValueError, OverflowError):
            invalid.add(key)
            continue
        try:
            value = Decimal(str(row.get("Mv")))
            if not value.is_finite() or value < 0:
                value = None
        except (InvalidOperation, ValueError):
            value = None
        previous = readings[key].get(stamp)
        if stamp in readings[key] and previous != value:
            value = None
        readings[key][stamp] = value
    return {key: (sorted(points.items()), key in invalid) for key, points in readings.items()}


def sampling_policy(settings):
    """Shared transition policy for legacy task and unified energy reports."""
    if not settings.energy_sampling_change_at:
        return {}
    if not 60 <= settings.energy_new_max_gap_seconds <= 3600:
        raise ValueError("新采样间隔上限必须为 60—3600 秒")
    return {"sampling_change_at": local_time(settings.energy_sampling_change_at, settings.timezone),
            "new_max_gap_seconds": settings.energy_new_max_gap_seconds}


def sampling_notice(settings, historical_gap):
    policy = sampling_policy(settings)
    if not policy:
        return f"最大允许采样间隔 {historical_gap} 秒。"
    stamp = policy["sampling_change_at"].isoformat(sep=" ")
    return (f"采样规则切换时间 {stamp}（{settings.timezone}）；"
            f"此前允许间隔 {historical_gap} 秒，此后 {policy['new_max_gap_seconds']} 秒；"
            "超过上限的时段标为数据缺口，不计入有效耗量。")


def interval_consumption(series, start, end, *, max_gap_seconds, interpolate, factor=Decimal("1"),
                         sampling_change_at=None, new_max_gap_seconds=180, estimate=False):
    points, invalid = series
    result = {"value": None, "status": "missing"}
    if invalid:
        return {**result, "status": "invalid_reading"}
    times = [stamp for stamp, _ in points]
    left = bisect_right(times, start) - 1
    right = bisect_left(times, end)
    allow_estimate = estimate and interpolate
    if (left < 0 or right >= len(points)) and not allow_estimate:
        return result
    left, right = max(0, left), min(right, len(points) - 1)
    selected = points[left:right + 1]
    if len(selected) < 2:
        return {**result, "sample_count": len(selected)}
    if any(value is None for _, value in selected):
        return {**result, "status": "invalid_reading"}
    sparse = False
    for (t0, v0), (t1, v1) in zip(selected, selected[1:]):
        if v1 < v0:
            return {**result, "status": "counter_reset"}
        # Check each side separately when samples straddle the acquisition change.
        # A normal final historical sample must not be judged as a one-minute sample.
        before_end = min(t1, sampling_change_at) if sampling_change_at else t1
        old_gap = max(0, (before_end - t0).total_seconds())
        new_gap = max(0, (t1 - max(t0, sampling_change_at)).total_seconds()) if sampling_change_at else 0
        if old_gap > max_gap_seconds or new_gap > new_max_gap_seconds:
            if not allow_estimate:
                return {**result, "status": "gap"}
            sparse = True
    estimated = start not in times or end not in times
    if estimated and not interpolate:
        return result

    def boundary(stamp):
        i = bisect_left(times, stamp)
        if times[i] == stamp:
            return points[i][1]
        t0, v0 = points[i - 1]
        t1, v1 = points[i]
        ratio = Decimal(str((stamp - t0).total_seconds())) / Decimal(str((t1 - t0).total_seconds()))
        return v0 + (v1 - v0) * ratio

    covered_start = max(start, selected[0][0])
    covered_end = min(end, selected[-1][0])
    if covered_end <= covered_start:
        return result
    partial = covered_start != start or covered_end != end
    try:
        start_value, end_value = boundary(covered_start), boundary(covered_end)
        covered_seconds = Decimal(str((covered_end - covered_start).total_seconds()))
        task_seconds = Decimal(str((end - start).total_seconds()))
        observed = (end_value - start_value) * factor
        consumption = observed * task_seconds / covered_seconds if partial else observed
        consumption = consumption.quantize(Decimal("0.001"))
    except ArithmeticError:
        return {**result, "status": "invalid_reading"}
    estimation = {}
    if sparse or partial:
        estimation = {
            "estimation_method": "average_rate" if partial else "cumulative_difference",
            "covered_start": covered_start, "covered_end": covered_end,
            "covered_seconds": covered_seconds, "task_seconds": task_seconds,
            "observed_consumption": observed,
            "observed_start_reading": start_value, "observed_end_reading": end_value,
        }
    return {
        "value": consumption,
        "status": "estimated" if sparse or partial else "interpolated" if estimated else "measured",
        "start_reading": start_value if covered_start == start else None,
        "end_reading": end_value if covered_end == end else None,
        "first_sample_time": selected[0][0], "first_sample_value": selected[0][1],
        "last_sample_time": selected[-1][0], "last_sample_value": selected[-1][1],
        "sample_count": len(selected), "conversion_factor": factor,
        **estimation,
    }
