"""One freshness policy for every statistics provider."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import Settings


REPORT_CACHE_FIELDS = {
    'current_seconds': 'leadership_report_cache_seconds',
    'history_seconds': 'leadership_report_history_cache_seconds',
    'query_seconds': 'report_data_cache_seconds',
    'refresh_seconds': 'report_background_refresh_seconds',
}


def report_cache_seconds(settings: Settings, *, historical: bool = False) -> int:
    value = settings.leadership_report_history_cache_seconds if historical else settings.leadership_report_cache_seconds
    return value if value > 0 else (86400 if historical else 300)


def report_ttl(settings: Settings, end: date | str | None = None, *, today: date | None = None) -> int:
    today = today or datetime.now(ZoneInfo(settings.timezone)).date()
    selected = date.fromisoformat(end) if isinstance(end, str) and end else end
    return report_cache_seconds(settings, historical=bool(selected and selected < today.replace(day=1)))


def report_refresh_seconds(settings: Settings) -> int:
    return max(30, settings.report_background_refresh_seconds)
