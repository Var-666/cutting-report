from tests.postgres_fixtures import state_settings
from tests.line_fixtures import source_client
from app.database.source_registry import DatabaseSourceRegistry
from tests.line_fixtures import as_line_services, runtime_line
"""BM enrichment is tested offline; production metrics and confirmations stay unchanged."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
import logging

import pytest

from tests.line_fixtures import CUTTING_SCOPES, cutting_settings, configuration
from app.device_config import load_line_catalog
from app.core.request_cancellation import SqlRequestCancelled
from app.core.report_deadline import ReportOperationTimeout
from app.database.bm_projects import BmProjectRepository
from app.database.source_registry import SourceConflict
from app.database.sqlserver import SqlServerClient
from app.modules.cutting.confirmations import ConfirmationRepository
from app.database.users import UserRepository
from app.modules.cutting.workshop_repository import WorkshopReportRepository
from app.models.database_sources import DatabaseSourceRequest
from app.models.domain import ReportFilter, WorkshopRecord
from app.services.application_runtime import build_runtime
from app.modules.cutting.service import QueryService
from app.services.ship_name_service import ShipNameService
from app.modules.cutting.workshop import WorkshopService


def record(task='1', **changes):
    return WorkshopRecord(task_id=task, project_code='CMYZ0170', ship_no='CMYZ0170', segment_name='S1',
        program_no='N1', cut_length=Decimal('1.2'), groove_length=Decimal('0.1'), work_time=Decimal('60'),
        production_time=datetime(2026, 11, 1, 8), **changes)


def test_repository_batches_normalizes_and_refuses_ambiguous_or_partial_matches():
    calls = []
    def fetch(sql, parameters):
        calls.append((sql, parameters))
        assert sql.count('?') == len(parameters) <= 500
        assert 'LIKE' not in sql
        rows = [{'project_number': ' cmyz0170 ', 'ship_name': ' 18600DWT-02 '},
                {'project_number': 'DUP', 'ship_name': 'first'}, {'project_number': 'dup', 'ship_name': 'second'},
                {'project_number': '00.120199.43620001CMYZ0171', 'ship_name': 'prefixed'},
                {'project_number': 'EMPTY', 'ship_name': '  '}]
        return [row for row in rows if row['project_number'].strip().upper() in parameters]
    repository = BmProjectRepository(SimpleNamespace(fetch_all=fetch))
    names = repository.ship_names([' cmyz0170 ', 'DUP', 'CMYZ0171', 'EMPTY', 'MISSING', ''] + [f'P{i}' for i in range(501)])
    assert names['CMYZ0170'] == '18600DWT-02'
    assert names['DUP'] is names['CMYZ0171'] is names['EMPTY'] is names['MISSING'] is None
    assert len(calls) == 2


def test_enrichment_caches_without_mutating_records_and_fails_without_losing_tasks(caplog):
    rows = [record(), replace(record('2'), project_code=None), replace(record('3'), project_code='MISSING')]
    calls = []
    repository = SimpleNamespace(ship_names=lambda numbers: calls.append(numbers) or {'CMYZ0170': '18600DWT-02'})
    service = ShipNameService(repository, cache_seconds=600)
    mapped = service.enrich(rows)
    assert [row.ship_no for row in mapped] == ['18600DWT-02', None, None]
    assert rows[0].ship_no == 'CMYZ0170' and mapped[0] is not rows[0]
    assert [row.cut_length for row in mapped] == [row.cut_length for row in rows]
    service.enrich(rows)
    assert len(calls) == 1
    service.enrich(rows, force=True)
    assert len(calls) == 2
    def fail(numbers):
        raise RuntimeError('private-password-and-query')
    service.repository.ship_names = fail
    with caplog.at_level(logging.WARNING):
        fallback = service.enrich([record(), mapped[0]], force=True)
    assert [row.ship_no for row in fallback] == [None, '18600DWT-02']
    assert len(fallback) == 2 and 'private-password' not in caplog.text
    for error in (SqlRequestCancelled('cancelled'), ReportOperationTimeout('timeout')):
        service.repository.ship_names = lambda _, error=error: (_ for _ in ()).throw(error)
        with pytest.raises(type(error)):
            service.enrich(rows, force=True)


@pytest.mark.parametrize('kind', ['summary', 'workshop', 'equipment'])
def test_all_report_queries_map_and_filter_names_before_statistics(kind):
    service = QueryService(cutting_settings(data_source_mode='sqlserver', report_data_cache_seconds=600), ship_names=ShipNameService(
        SimpleNamespace(ship_names=lambda numbers: {'CMYZ0170': '18600DWT-02'}), cache_seconds=600), client=source_client(cutting_settings(data_source_mode='sqlserver', report_data_cache_seconds=600)))
    calls = []
    def fetch(sql, values):
        calls.append(values)
        base = dict(task_id='1', project_code='CMYZ0170', ship_no='CMYZ0170', equipment_id='Cutter01',
            segment_name='S1', program_no='N1', cut_length=1, groove_length=0, work_time=60,
            production_time=datetime(2026, 11, 1, 8))
        return [base, {**base, 'task_id':'2', 'project_code':'MISSING', 'ship_no':'MISSING'}]
    service.repository.client = SimpleNamespace(fetch_all=fetch)
    filters = ReportFilter(report_date=date(2026, 11, 1), ship_no='18600DWT-02')
    def records(result):
        return result.records if kind == 'equipment' else getattr(result, f'{kind}_records')
    rows = records(getattr(service, f'fetch_{kind}_data')(filters))
    assert len(rows) == 1 and rows[0].ship_no == '18600DWT-02' and rows[0].project_code == 'CMYZ0170'
    assert calls[0][-2:] == (None, None)  # name is never sent as a production project-number predicate
    rows[0].ship_no = 'caller-change'
    assert records(getattr(service, f'fetch_{kind}_data')(filters))[0].ship_no == '18600DWT-02'
    assert len(calls) == 1
    unfiltered = records(getattr(service, f'fetch_{kind}_data')(replace(filters, ship_no=None)))
    assert len(unfiltered) == 2 and sum(row.work_time for row in unfiltered) == 120
    assert unfiltered[1].ship_no is None


def test_ship_options_follow_date_and_project_and_use_shared_mapping_cache():
    service = QueryService(cutting_settings(data_source_mode='sqlserver'), ship_names=ShipNameService(
        SimpleNamespace(ship_names=lambda numbers: {'CMYZ0170': '18600DWT-02', 'P2': '18600DWT-02'}), cache_seconds=600), client=source_client(cutting_settings(data_source_mode='sqlserver')))
    calls = []
    service.repository.client = SimpleNamespace(fetch_all=lambda sql, values: calls.append(values) or [{'project_name':'CMYZ0170'},{'project_name':'P2'}])
    filters = ReportFilter(report_date=date(2026,11,1),project_code='CMYZ0170')
    assert service.list_ships(filters) == ['18600DWT-02']
    assert calls[0][-2:] == ('CMYZ0170','CMYZ0170')


@pytest.fixture
def workshop(state_database, tmp_path):
    repo = WorkshopReportRepository(state_database, scope_machine_no='Cutter01')
    confirmations = ConfirmationRepository(state_database, scope_machine_no='Cutter01')
    now = '2026-11-01T08:00:00+08:00'
    admin = UserRepository(state_database).bootstrap_admin(username='admin', display_name='管理员',password='test-password',now=now)
    return repo, confirmations, admin, now


def test_snapshot_stores_separate_ship_name_and_backfills_without_revision_or_audit_changes(workshop):
    repo, confirmations, admin, now = workshop
    report = repo.sync_daily_report(report_date='2026-11-01',source_mode='sqlserver',records=[record()],user=admin,now=now)
    confirmation = confirmations.confirm_task(task_id='1',report_id=report['report_id'],user=admin,now=now)
    before = repo.get_daily_report_records('2026-11-01')[1][0]
    before_confirmation = confirmations.get_confirmations(['1'])['1']
    repo.sync_daily_report(report_date='2026-11-01',source_mode='sqlserver',records=[replace(record(),ship_no='18600DWT-02')],user=admin,now=now)
    after = repo.get_daily_report_records('2026-11-01')[1][0]
    assert after.project_code == 'CMYZ0170' and after.ship_no == '18600DWT-02'
    assert after.snapshot_revision == before.snapshot_revision
    assert after.confirmed_by == before.confirmed_by == '管理员'
    assert confirmations.get_confirmations(['1'])['1'] == before_confirmation
    repo.sync_daily_report(report_date='2026-11-01',source_mode='sqlserver',
        records=[replace(record(),work_time=Decimal('99'))],user=admin,now=now)
    changed = repo.get_daily_report_records('2026-11-01')[1][0]
    assert changed.ship_no == '18600DWT-02' and changed.work_time == Decimal('99')
    assert changed.snapshot_revision == after.snapshot_revision + 1
    assert confirmations.get_confirmations(['1'])['1'] == before_confirmation
    # All snapshot insert paths: initial, append, and fill a previously empty report.
    repo.sync_daily_report(report_date='2026-11-01',source_mode='sqlserver',records=[record(),replace(record('2'),ship_no='Second ship')],user=admin,now=now)
    assert repo.get_daily_report_records('2026-11-01')[1][1].ship_no == 'Second ship'
    repo.create_daily_report(report_date='2026-11-02',source_mode='sqlserver',records=[],user=admin,now=now)
    repo.sync_daily_report(report_date='2026-11-02',source_mode='sqlserver',records=[replace(record(),ship_no='New ship')],user=admin,now=now)
    assert repo.get_daily_report_records('2026-11-02')[1][0].ship_no == 'New ship'


def test_historical_snapshot_page_enriches_but_confirm_does_not_query_bm(workshop):
    repo, confirmations, admin, now = workshop
    repo.sync_daily_report(report_date='2026-11-01',source_mode='sqlserver',records=[record()],user=admin,now=now)
    query = QueryService(cutting_settings(data_source_mode='sqlserver'),ship_names=ShipNameService(
        SimpleNamespace(ship_names=lambda numbers:{'CMYZ0170':'18600DWT-02'}),cache_seconds=600), client=source_client(cutting_settings(data_source_mode='sqlserver')))
    service = WorkshopService(repo,confirmations,cutting_settings(),query_service=query)
    payload = service.list_tasks(date(2026,11,1),None,'all','manager',1,20,False,admin)
    assert payload['items'][0]['ship_no'] == '18600DWT-02' and payload['total'] == 1
    from app.models.workshop_requests import ConfirmSnapshotTaskRequest
    query.enrich_ship_names = lambda _: pytest.fail('confirmation must not require BM')
    service.confirm_task('1',ConfirmSnapshotTaskRequest(report_date=date(2026,11,1),snapshot_revision=1),admin)


def test_runtime_range_report_keeps_tasks_and_device_confirmations_when_bm_is_down(postgres_state, tmp_path, monkeypatch):
    from app.database.source_registry import source_configs_json
    from app.models.database_sources import DatabaseSourceConfig
    bm = DatabaseSourceConfig(id='bm', name='船名库', engine='sqlserver', host='offline', port=1433,
                              database='bm', username='reader', password='private')
    base = configuration(tmp_path, real=True)
    cfg = replace(base, database_sources_json=source_configs_json(DatabaseSourceRegistry.from_json(base.database_sources_json).configs() + [bm]))
    def fetch(client, sql, parameters):
        if client.source == 'bm':
            raise RuntimeError('BM unavailable')
        return [dict(task_id='1', project_code='CMYZ0170', ship_no='CMYZ0170', segment_name='S1',
                     program_no='N1', cut_length=1.2, groove_length=0.1, work_time=60,
                     production_time=datetime(2026, 11, 1, 8))]
    monkeypatch.setattr(SqlServerClient, 'fetch_all', fetch)
    runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
    try:
        now = '2026-11-01T08:00:00+08:00'
        admin = runtime.users.bootstrap_admin(username='admin', display_name='管理员', password='test-password', now=now)
        first = runtime.database_runtime.snapshot().lines['Cutter01'].service
        report = first.workshop.create_daily_report(report_date='2026-11-01', source_mode='sqlserver',
                                                    records=[record()], user=admin, now=now)
        first.confirmations.confirm_task(task_id='1', report_id=report['report_id'], user=admin, now=now)
        for device_id, confirmed in [('Cutter01', True), ('Cutter02', False)]:
            device = runtime.database_runtime.snapshot().lines[device_id].service
            service = WorkshopService(device.workshop, device.confirmations, device.settings,
                                      query_service=device.query_service, range_report_service=device.range_report_service)
            result = service.list_range_tasks(date(2026, 11, 1), date(2026, 11, 1), None, 'all', 1, 20)
            assert result['total'] == 1
            row = result['items'][0]
            assert row['project_code'] == 'CMYZ0170' and row['ship_no'] is None
            assert bool(row['confirmed_by']) is confirmed
            assert row['cut_length'] == 1.2 and row['work_time'] == 60
    finally:
        runtime.database_runtime.close()


def test_bm_runtime_registration_hot_reload_protection_and_old_request(postgres_state, tmp_path,monkeypatch):
    cfg=configuration(tmp_path, real=True)
    runtime=build_runtime(state_settings(cfg,postgres_state),role='web')
    initial=runtime.database_runtime.snapshot()
    assert initial.lines['Cutter01'].service.query_service.ship_names is None
    payload=DatabaseSourceRequest(id='bm',name='船名库',engine='sqlserver',host='test-bm',port=1433,
        database='bm',username='reader',password='private')
    runtime.database_runtime.save_source(payload,create=True)
    first=runtime.database_runtime.snapshot()
    query=first.lines['Cutter01'].service.query_service
    mapper=query.ship_names
    assert mapper is not None and first.lines['Cutter01'] is not initial.lines['Cutter01']
    assert mapper is first.lines['Cutter02'].service.query_service.ship_names
    assert mapper.repository.client is first.database_sources.client('bm')
    assert first.database_sources.view('bm').references == ['切割报表船名关联']
    with pytest.raises(SourceConflict): runtime.database_runtime.delete_source('bm')
    runtime.database_runtime.save_source(payload.model_copy(update={'host':'other-bm'}))
    second=runtime.database_runtime.snapshot()
    assert mapper.repository.client.connection_config()['host']=='test-bm'
    assert second.lines['Cutter01'].service.query_service.ship_names.repository.client.connection_config()['host']=='other-bm'
    runtime.database_runtime.set_source_enabled('bm',False)
    assert runtime.database_runtime.snapshot().lines['Cutter01'].service.query_service.ship_names is None
    assert query.ship_names is mapper
    with pytest.raises(ValueError):
        runtime.database_runtime.save_source(payload.model_copy(update={'engine':'mysql'}))
    assert runtime.database_runtime.snapshot().database_sources.get('bm').engine == 'sqlserver'
    worker = build_runtime(state_settings(cfg,postgres_state), role='worker')
    assert not worker.database_runtime.snapshot().database_sources.get('bm').enabled
    worker.database_runtime.close()
    runtime.database_runtime.close()


def test_snapshot_page_and_html_export_share_bm_names_and_filtered_totals(state_database, tmp_path, monkeypatch):
    from app.database.metadata import MetadataRepository
    from app.models.domain import AnalysisResult
    from app.models.workshop_requests import ConfirmTaskRequest
    from app.services.pipeline import ReportPipeline
    vendor = tmp_path/'vendor'
    vendor.mkdir()
    (vendor/'echarts.min.js').write_text('window.echarts={init(){return {setOption(){},resize(){}}}}')
    cfg = cutting_settings(project_root=tmp_path,data_source_mode='sqlserver',llm_enabled=False)
    metadata = MetadataRepository(state_database,device_id=cfg.device_id)
    query = QueryService(cfg,ship_names=ShipNameService(
        SimpleNamespace(ship_names=lambda numbers:{'CMYZ0170':'18600DWT-02'}),cache_seconds=600), client=source_client(cfg))
    rows = [dict(task_id='1',project_code='CMYZ0170',ship_no='CMYZ0170',segment_name='S1',program_no='N1',
                cut_length=Decimal('1.2'),groove_length=Decimal('0.1'),work_time=Decimal('60'),
                production_time=datetime(2026,11,1,8),source_updated_at=datetime(2026,11,1,8,1)),
            dict(task_id='2',project_code='MISSING',ship_no='MISSING',segment_name='S2',program_no='N2',
                cut_length=Decimal('2.4'),groove_length=Decimal('0.2'),work_time=Decimal('120'),
                production_time=datetime(2026,11,1,9),source_updated_at=datetime(2026,11,1,9,2))]
    query.repository.client = SimpleNamespace(fetch_all=lambda sql, values: [
        row for row in rows if values[1] <= row['source_updated_at'] < values[2]
    ])
    pipeline = ReportPipeline(cfg,metadata,query_service=query)
    monkeypatch.setattr(pipeline.analysis_service, 'analyze_workshop', lambda *args, **kwargs: AnalysisResult(
        status='success', overview='测试分析', features='', alerts='', suggestions=''))
    repo = pipeline.workshop_repository
    now = '2026-11-01T10:00:00+08:00'
    admin = UserRepository(state_database).bootstrap_admin(
        username='admin', display_name='管理员', password='test-password', now=now)
    service = WorkshopService(repo, pipeline.range_service.confirmations, cfg, query_service=query)
    service.generate_daily_report(ConfirmTaskRequest(report_date=date(2026,11,1)),admin)
    page = service.list_tasks(date(2026,11,1),None,'all','manager',1,20,False,admin)
    assert page['total']==2
    assert [row['ship_no'] for row in page['items']]==[None,'18600DWT-02']
    assert repo.get_daily_report_records('2026-11-01')[1][0].ship_no=='18600DWT-02'
    metadata.create_job(report_id='bm-report',report_date='2026-11-01',project_code=None,ship_no='18600DWT-02',
        request_id='offline-bm',created_at=now,report_type='workshop')
    job=metadata.claim_next_job(now)
    pipeline.run(job)
    saved=metadata.get_job('bm-report')
    assert saved['status']=='completed',saved
    assert saved['task_count']==1
    html=(cfg.reports_dir/saved['relative_path']).read_text()
    assert '<th>船名</th>' in html and '18600DWT-02' in html and 'CMYZ0170' in html
    assert '<td>MISSING</td>' not in html


@pytest.mark.parametrize(('project','ship','segment','expected'), [
    (None,None,None,(None,None,None)),
    ('','','',(None,None,None)),
    ('   ','   ','   ',(None,None,None)),
    (None,'已有船名','804C',(None,'已有船名','804C')),
    ('CMYZ0170',None,None,('CMYZ0170',None,None)),
    ('CMYZ0170','18600DWT-02','804C',('CMYZ0170','18600DWT-02','804C')),
    ('非船','非船','非船',('非船','非船','非船')),
])
def test_report_task_fields_show_red_unknown_only_for_missing_values(project,ship,segment,expected):
    from app.services.report_service import ReportService
    from app.models.domain import to_primitive

    row = replace(record(),project_code=project,ship_no=ship,segment_name=segment)
    values = {**to_primitive(row), 'gas_consumption': None, 'electricity_consumption': None}
    template = ReportService(cutting_settings()).environment.get_template('report.html.j2')
    summary = dict(task_count=1, total_cut_length=0, total_groove_length=0, total_work_time=0,
                   avg_cut_length=0, avg_groove_length=0, avg_work_time=0, total_gas_consumption=None,
                   total_electricity_consumption=None, gas_record_count=0, electricity_record_count=0)
    meta = dict(title='生产报表', report_id='labels', generated_at='2026-11-01T18:00:00+08:00',
                period_start='2026-11-01', period_end='2026-11-01')
    report = dict(summary=summary, equipment_summary=summary, filters={}, units={},
                  energy_month=dict(rows=[], total_task_count=0, total_work_time_seconds=0,
                                    summary=dict(electricity={'value': None}, gas={'value': None})),
                  workshop_records=[values], equipment_records=[values],
                  charts=dict(length_combo={}, equipment_detail={}, equipment_month={}))
    unknown = '<span class="lp-attention-badge lp-tone-critical">未知</span>'
    for kind in ('workshop', 'equipment'):
        html = template.render(report={**report, 'meta': {**meta, 'report_type': kind}}, echarts_source='')
        assert ''.join(f'<td>{value if value is not None else unknown}</td>' for value in expected) in html
    assert (row.project_code,row.ship_no,row.segment_name) == (project,ship,segment)
