from dataclasses import replace
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from app.database.coordination import CoordinationRepository
from app.database.postgres import execution_scope
from app.services.pipeline import ReportPipeline
from app.services.query_service import QueryService
from app.services.llm_service import LlmRequestTimeout
from app.workers.report_worker import ReportWorker
from tests.analysis_fakes import model_analysis
from tests.automation_fixtures import complete_report, NOW, STAMP
from tests.line_fixtures import source_client


def legacy_delivery(workflow, status='unavailable'):
    repository, metadata, service, run = workflow
    assert service._create_report_job(run, NOW)
    report_id = repository.get_run(run['run_id'])['report_id']
    complete_report(metadata, report_id)
    with metadata.connect() as connection:
        connection.execute('UPDATE report_jobs SET ai_status=%s WHERE report_id=%s', (status, report_id))
    service.report_completed(report_id, NOW)
    service.email_service = Mock()
    return repository, metadata, service, run, report_id


@pytest.mark.parametrize('status', ['unavailable', None])
def test_legacy_mail_waits_for_new_success_and_resumes_same_delivery_once(workflow, status):
    repository, metadata, service, run, old_id = legacy_delivery(workflow, status)
    old_delivery = repository.delivery_for_report(old_id)
    old_html = (service.settings.reports_dir / 'r.html').read_bytes()
    queue = CoordinationRepository(metadata.database)
    automation_owner = queue.acquire('automation', ttl=120)
    with execution_scope(metadata.database, 'automation', automation_owner):
        assert service.process_next_email(NOW)
    service.email_service.send.assert_not_called()
    new_id = repository.get_run(run['run_id'])['report_id']
    assert new_id != old_id
    waiting = repository.delivery_for_report(old_id)
    assert waiting['status'] == 'analysis_wait' and waiting['attempt_count'] == 0
    assert repository.claim_delivery(STAMP) is None
    assert service.run_progress(run['run_id'])['status'] == 'analysis_wait'
    assert repository.summary()['delivery_pending'] == 1
    assert repository.list_deliveries(page=1, page_size=20)[0][0]['delivery_id'] == old_delivery['delivery_id']

    cfg = replace(service.settings, llm_enabled=True, data_source_mode='demo', demo_mode=True)
    cfg.echarts_vendor_path.parent.mkdir(exist_ok=True)
    cfg.echarts_vendor_path.write_text('window.echarts={};')
    pipeline = ReportPipeline(cfg, metadata, query_service=QueryService(cfg, client=source_client(cfg)))
    pipeline.analysis_service.llm.analyze_context = Mock(side_effect=LlmRequestTimeout('fixture outage'))
    worker = ReportWorker(cfg, metadata, pipeline=pipeline)
    consumer_owner = queue.acquire('worker:analysis-delivery', ttl=120)
    with execution_scope(metadata.database, 'worker:analysis-delivery', consumer_owner):
        worker._process_report(metadata.claim_next_job(STAMP), ZoneInfo(cfg.timezone))
    assert metadata.get_job(new_id)['status'] == 'pending'
    assert repository.claim_delivery(STAMP) is None
    service.email_service.send.assert_not_called()
    pipeline.analysis_service.llm.analyze_context = model_analysis
    with metadata.connect() as connection:
        connection.execute('UPDATE report_jobs SET analysis_next_attempt=0 WHERE report_id=%s', (new_id,))
    with execution_scope(metadata.database, 'worker:analysis-delivery', consumer_owner):
        pipeline.run(metadata.claim_next_job(STAMP))
    # A missing completion callback is recovered after the report commits.
    with execution_scope(metadata.database, 'automation', automation_owner):
        assert service.reconcile_runs(NOW) == 1
        assert service.reconcile_runs(NOW) == 0
        ready = repository.delivery_for_report(new_id)
        assert ready['delivery_id'] == old_delivery['delivery_id'] and ready['status'] == 'pending'
        service.email_service.send.return_value = ready['message_id']
        assert service.process_next_email(NOW)
        assert not service.process_next_email(NOW)
    service.email_service.send.assert_called_once()
    path = service.email_service.send.call_args.kwargs['report_path']
    assert '本期生产记录已整理' in path.read_text() and '智能分析暂不可用' not in path.read_text()
    assert (service.settings.reports_dir / 'r.html').read_bytes() == old_html
    assert repository.delivery_for_report(new_id)['recipient_results']['manager@example.com']['status'] == 'accepted'


@pytest.mark.parametrize('recipient_status', ['accepted', 'unknown'])
def test_partial_acceptance_never_regenerates_or_resends_legacy_report(workflow, recipient_status):
    repository, metadata, service, run, old_id = legacy_delivery(workflow)
    with repository.connect() as connection:
        connection.execute('UPDATE email_deliveries SET recipient_results_json=%s WHERE report_id=%s',
            (json.dumps({'already@example.com': {'status': recipient_status}}), old_id))
    assert service.process_next_email(NOW)
    delivery = repository.delivery_for_report(old_id)
    assert delivery['status'] == ('failed' if recipient_status == 'accepted' else 'unknown')
    assert delivery['recipient_results']['already@example.com']['status'] == recipient_status
    assert repository.get_run(run['run_id'])['report_id'] == old_id
    assert metadata.list_reports(page=1, page_size=10)[1] == 1
    if recipient_status == 'accepted':
        assert repository.retry_delivery(delivery['delivery_id'], now=STAMP)
        assert service.process_next_email(NOW)
    else:
        assert not repository.retry_delivery(delivery['delivery_id'], now=STAMP)
        assert not service.process_next_email(NOW)
    service.email_service.send.assert_not_called()


def test_failed_rebuild_is_visible_and_manual_retry_prepares_a_new_report(workflow):
    repository, metadata, service, run, old_id = legacy_delivery(workflow)
    service.process_next_email(NOW)
    new_id = repository.get_run(run['run_id'])['report_id']
    metadata.update_status(new_id, 'failed', 'source failed', error_message='source failed')
    service.report_failed(new_id, 'source failed', NOW)
    delivery = repository.delivery_for_report(old_id)
    assert delivery['status'] == 'failed' and delivery['last_error'] == 'source failed'
    assert service.run_progress(run['run_id'])['status'] == 'failed'
    assert repository.retry_delivery(delivery['delivery_id'], now=STAMP)
    service.process_next_email(NOW)
    assert repository.get_run(run['run_id'])['report_id'] not in {old_id, new_id}
    assert repository.delivery_for_report(old_id)['status'] == 'analysis_wait'
    service.email_service.send.assert_not_called()


@pytest.mark.parametrize('status', ['sent', 'unknown', 'sending'])
def test_sent_unknown_and_inflight_legacy_mail_are_never_requeued(workflow, status):
    repository, metadata, service, run, old_id = legacy_delivery(workflow)
    with repository.connect() as connection:
        connection.execute('UPDATE email_deliveries SET status=%s WHERE report_id=%s', (status, old_id))
    assert not service.process_next_email(NOW)
    assert repository.get_run(run['run_id'])['report_id'] == old_id
    assert repository.delivery_for_report(old_id)['status'] == status
    service.email_service.send.assert_not_called()


def test_parallel_delivery_consumers_create_only_one_replacement(workflow):
    from concurrent.futures import ThreadPoolExecutor
    repository, metadata, service, run, old_id = legacy_delivery(workflow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.process_next_email(NOW), range(2)))
    assert sorted(results) == [False, True]
    assert metadata.list_reports(page=1, page_size=10)[1] == 2
    assert repository.delivery_for_report(old_id)['status'] == 'analysis_wait'
    service.email_service.send.assert_not_called()


def test_rebuild_transaction_failure_preserves_original_links_and_retries_without_smtp(workflow, monkeypatch):
    from app.database.metadata import MetadataRepository
    repository, metadata, service, run, old_id = legacy_delivery(workflow)
    monkeypatch.setattr(MetadataRepository, 'create_job', Mock(side_effect=RuntimeError('temporary database failure')))
    assert service.process_next_email(NOW)
    assert repository.get_run(run['run_id'])['report_id'] == old_id
    assert metadata.list_reports(page=1, page_size=10)[1] == 1
    assert repository.delivery_for_report(old_id)['status'] == 'retry_wait'
    service.email_service.send.assert_not_called()


def test_retention_keeps_original_mail_and_report_while_replacement_waits(workflow):
    from app.database.retention import RetentionRepository
    repository, metadata, service, run, old_id = legacy_delivery(workflow)
    service.process_next_email(NOW)
    counts = RetentionRepository(metadata.database).cleanup_expired_records(report_cutoff='2027-01-01',
        analysis_cutoff=None, alarm_cutoff=None, dispatch_cutoff=None, audit_cutoff=None, queued_at=STAMP, batch_size=100)
    assert counts['reports'] == 0
    assert metadata.get_job(old_id) is not None
    assert repository.delivery_for_report(old_id)['status'] == 'analysis_wait'
    assert repository.list_deliveries(page=1, page_size=20)[1] == 1
