"""Equal-progress comparisons for production days and calendar periods."""
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


def business_day_window(start: date, end: date, zone: tzinfo) -> Window:
    return datetime.combine(start - DAY, CUTOFF, zone), datetime.combine(end, CUTOFF, zone)


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

    calendar_end = day if day == local.date() else day + DAY

    def calendar_period(label: str, comparison_label: str, first: date, prior_first: date):
        prior_end = min(prior_first + (calendar_end - first), first)
        comparable_end = first + (prior_end - prior_first)
        start = datetime.combine(first, time.min, zone)
        return (label, comparison_label, (start, datetime.combine(calendar_end, time.min, zone)),
                (datetime.combine(prior_first, time.min, zone), datetime.combine(prior_end, time.min, zone)),
                (start, datetime.combine(comparable_end, time.min, zone)))

    today = "今日" if day == local.date() else "当日"
    current_day = business_day_window(day, day, zone)
    previous_day = day - DAY
    daily = (today, "上一业务日", current_day, business_day_window(previous_day, previous_day, zone), current_day)
    month = day.replace(day=1)
    prior_month = (month - DAY).replace(day=1)
    if grouping == "month_quarter_year":
        quarter = month.replace(month=(month.month - 1) // 3 * 3 + 1)
        prior_quarter_end = quarter - DAY
        prior_quarter = prior_quarter_end.replace(month=prior_quarter_end.month - 2, day=1)
        year = month.replace(month=1)
        periods = (
            daily,
            calendar_period("本月", "上月同期", month, prior_month),
            calendar_period("本季度", "上季度同期", quarter, prior_quarter),
            calendar_period("本年", "上年同期", year, year.replace(year=year.year - 1)),
        )
    else:
        week = day - timedelta(days=day.weekday())
        periods = (
            daily,
            calendar_period("本周累计", "上周同期", week, week - timedelta(days=7)),
            calendar_period("本月累计", "上月同期", month, prior_month),
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
