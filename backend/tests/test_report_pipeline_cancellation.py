from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services.pipeline import ReportPipeline, ReportPipelineCancelled
from app.workers.report_worker import ReportWorker


class _Metadata:
    def __init__(self):
        self.failed = []

    def update_status(self, *args, **kwargs):
        self.failed.append((args, kwargs))


class _Pipeline:
    def __init__(self):
        self.received_check = None

    def run(self, job, *, cancellation_check):
        self.received_check = cancellation_check
        raise ReportPipelineCancelled("shutdown")


def test_worker_shutdown_cancellation_does_not_mark_report_failed():
    metadata = _Metadata()
    pipeline = _Pipeline()
    settings = SimpleNamespace(
        worker_poll_seconds=0.01,
        worker_stall_timeout_seconds=10,
    )
    worker = ReportWorker(settings, metadata, pipeline)
    worker.stop_event.set()

    worker._process_report(
        {"report_id": "report-1"},
        ZoneInfo("Asia/Shanghai"),
    )

    assert pipeline.received_check is not None
    assert pipeline.received_check() is True
    assert metadata.failed == []


def test_pipeline_checkpoint_raises_before_starting_a_new_stage():
    pipeline = object.__new__(ReportPipeline)
    pipeline._cancellation_check = lambda: True

    with pytest.raises(ReportPipelineCancelled):
        pipeline._ensure_active()
