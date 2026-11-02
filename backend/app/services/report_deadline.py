"""Compatibility imports; the shared implementation lives in app.core."""
from app.core.report_deadline import (
    ReportOperationTimeout as ReportOperationTimeout,
    ensure_time_remaining as ensure_time_remaining,
    operation_deadline as operation_deadline,
    remaining_seconds as remaining_seconds,
    report_deadline as report_deadline,
)
