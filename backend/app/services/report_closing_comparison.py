"""Equal-progress comparisons for reports that close each production day at 17:00."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from zoneinfo import ZoneInfo


CUTOFF = time(17)
DAY = timedelta(days=1)
Window = tuple[datetime, datetime]
Totals = dict[str, Decimal | int | None]
ClosingGrouping = Literal["day_week_month", "month_quarter_year"]


def closing_bucket(stamp: datetime) -> datetime:
    return stamp.replace(hour=17 if stamp.hour >= 17 else 0, minute=0, second=0, microsecond=0)


def sum_closing_totals(window: Window, buckets: Mapping[datetime, Mapping[str, Decimal | int]], metrics: Iterable[str]) -> Totals:
    start, end = window
    included = [values for stamp, values in buckets.items() if start <= stamp < end]
    return {key: sum(row[key] for row in included) for key in metrics}


def closing_comparison(
    start_date: date,
    end_date: date,
    reference_at: datetime,
    timezone: str | tzinfo,
    load_totals: Callable[[tuple[Window, ...]], dict[Window, Totals]],
    *,
    grouping: ClosingGrouping = "day_week_month",
) -> list[dict]:
    zone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    local = reference_at.astimezone(zone) if reference_at.tzinfo else reference_at.replace(tzinfo=zone)
    latest_closed_day = local.date() - DAY if local.time() < CUTOFF else local.date()
    day = min(end_date, latest_closed_day)
    if day < start_date or day < date(1, 3, 1):
        return []
    if grouping == "month_quarter_year" and day.year <= 2:
        return []

    def window(first: date, last: date, midnight: bool) -> Window:
        start = datetime.combine(first, time.min, zone) if midnight else datetime.combine(first - DAY, CUTOFF, zone)
        return start, datetime.combine(last, CUTOFF, zone)

    def period(label: str, comparison_label: str, first: date, prior_first: date, *, midnight: bool = False):
        prior_last = min(prior_first + (day - first), first - DAY)
        comparable_last = first + (prior_last - prior_first)
        return label, comparison_label, window(first, day, midnight), window(prior_first, prior_last, midnight), window(first, comparable_last, midnight)

    month = day.replace(day=1)
    prior_month = (month - DAY).replace(day=1)
    if grouping == "month_quarter_year":
        quarter = month.replace(month=(month.month - 1) // 3 * 3 + 1)
        prior_quarter_end = quarter - DAY
        prior_quarter = prior_quarter_end.replace(month=prior_quarter_end.month - 2, day=1)
        year = month.replace(month=1)
        periods = (
            period("本月", "上月同期", month, prior_month),
            period("本季度", "上季度同期", quarter, prior_quarter),
            period("本年", "上年同期", year, year.replace(year=year.year - 1)),
        )
    else:
        week = day - timedelta(days=day.weekday())
        today = "今日" if day == local.date() else "当日"
        periods = (
            period(today, "上一业务日", day, day - DAY),
            period("本周累计", "上周同期", week, week - timedelta(days=7), midnight=True),
            period("本月累计", "上月同期", month, prior_month, midnight=True),
        )
    windows = tuple(dict.fromkeys(item for _, _, current, previous, comparable in periods for item in (current, previous, comparable)))
    totals = load_totals(windows)

    columns = []
    for label, comparison_label, current, previous, comparable in periods:
        changes = {}
        for metric, baseline in totals[previous].items():
            value = totals[comparable][metric]
            changes[metric] = None
            if value is not None and baseline:
                delta = ((Decimal(value) - baseline) * 100 / baseline).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                changes[metric] = f"{delta:+.1f}%" if delta else "0.0%"
        columns.append({
            "label": label, "comparison_label": comparison_label,
            "start": current[0], "end": current[1], "comparable_end": comparable[1],
            "comparison_start": previous[0], "comparison_end": previous[1],
            "totals": totals[current], "previous_totals": totals[previous], "comparable_totals": totals[comparable],
            "changes": changes,
        })
    return columns
