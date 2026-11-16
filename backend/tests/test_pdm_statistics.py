from datetime import date
import sqlite3
from types import SimpleNamespace

import pytest
import sqlglot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import pdm_statistics as api
from app.api.auth import require_admin, require_user
from app.database.pdm_statistics import PdmStatisticsRepository
from app.models.pdm_statistics import PdmOptionsQuery, PdmStatisticsQuery
from app.services.pdm_statistics import PdmStatisticsService


class ReadOnlySource:
    """Execute the repository's SQL against isolated rows with T-SQL date conversion emulated."""
    def __init__(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        def parse_date(value):
            try:
                return date.fromisoformat(str(value).strip()[:10]).isoformat()
            except ValueError:
                return None
        self.db.create_function('pdm_date', 1, parse_date)
        self.db.create_function('pdm_raw', 1, lambda value: str(value).strip() or None if value is not None else None)
        self.db.executescript('''
            CREATE TABLE PDM_DocumentType(ID TEXT, Name TEXT, DesignDocType INTEGER);
            CREATE TABLE BasicConfig_ProjectInfo(ID TEXT, ProjectInfoName TEXT, ShipNo TEXT);
            CREATE TABLE PDM_DesignProfessional(ID TEXT, Name TEXT);
            CREATE TABLE PDM_DesignDocumentInfo(ID TEXT, DocumentTypeID TEXT, ProjectInfoID TEXT,
                DesignProfessionalID TEXT, ISAbandoned INTEGER, ArchivedTargetDate TEXT,
                ArchivedFeedBackDate TEXT, ResponsiblePerson TEXT,
                Custom7 TEXT, Custom11 TEXT, Custom12 TEXT, Custom13 TEXT, Custom14 TEXT,
                Custom15 TEXT, Custom16 TEXT, Custom17 TEXT);
            INSERT INTO PDM_DocumentType VALUES ('a','设备审批表',7),('b','设备技术协议',7),
                ('c','详设预审图',0),('d','详细设计',0),('e','完工文档',10),('a2','设备审批表',7);
            INSERT INTO BasicConfig_ProjectInfo VALUES ('p1','项目一','S1'),('p2','项目二','S2');
            INSERT INTO PDM_DesignProfessional VALUES ('f1','船体'),('f2','轮机');
        ''')
        self.sql = []

    def add(self, ident, type_id='a', project='p1', profession='f1', plan='2026-11-05', actual='2026-11-04', abandoned=0, owner='甲'):
        self.db.execute('INSERT INTO PDM_DesignDocumentInfo VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident, type_id, project, profession, abandoned, plan, actual, owner, '高', '机舱', 'A', '1', '是', '设计院', '一区', '结构'))

    def fetch_all(self, sql, parameters):
        self.sql.append((sql, parameters))
        sql = sql.replace(PdmStatisticsRepository.PLAN_DATE, 'pdm_date(d.ArchivedTargetDate)')
        sql = sql.replace(PdmStatisticsRepository.ACTUAL_DATE, 'pdm_date(d.ArchivedFeedBackDate)')
        sql = sql.replace(PdmStatisticsRepository.PLAN_VALUE, 'pdm_raw(d.ArchivedTargetDate)')
        sql = sql.replace(PdmStatisticsRepository.ACTUAL_VALUE, 'pdm_raw(d.ArchivedFeedBackDate)')
        parsed = sqlglot.parse_one(sql.replace('dbo.', ''), read='tsql')
        parsed = parsed.transform(lambda node: sqlglot.exp.Literal.string(node.this) if isinstance(node, sqlglot.exp.National) else node)
        converted = parsed.sql(dialect='sqlite')
        parameters = tuple(value.isoformat() if isinstance(value, date) else value for value in parameters)
        return [dict(row) for row in self.db.execute(converted, parameters)]


@pytest.fixture
def source():
    result = ReadOnlySource()
    yield result
    result.db.close()


def query(**changes):
    values = dict(document_kinds=['equipment', 'drawing'], start_date='2026-11-01', end_date='2026-11-10')
    values.update(changes)
    return PdmStatisticsQuery.model_validate(values)


def test_plan_population_cutoff_on_time_and_verified_type_names(source):
    source.add('ontime')
    source.add('future-completion', actual='2026-11-12')
    source.add('late', type_id='b', actual='2026-11-09T23:59:59')
    source.add('missing', type_id='b', actual='')
    source.add('outside-plan', plan='2026-10-30')
    source.add('abandoned', abandoned=1)
    source.add('generic-drawing', type_id='d')
    source.add('generic-completion', type_id='e')
    source.add('drawing', type_id='c', plan='2026-11-10T23:59:59', actual='2026-11-10T23:59:59')
    source.add('invalid-actual', type_id='c', actual='not-a-date')
    source.add('empty-plan', plan='')
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query())
    equipment, drawing = [table for table in report.tables if table.key.endswith(':summary')]
    assert [table.key for table in report.tables] == ['equipment:summary', 'drawing:summary', 'equipment:all:profession', 'equipment:all:project']
    assert equipment.totals.model_dump() == dict(total=5, completed=3, on_time=2, total_rate=60.0, on_time_rate=40.0)
    assert drawing.totals.total == 2 and drawing.totals.completed == drawing.totals.on_time == 1
    assert len(equipment.rows) == 5 and len(drawing.rows) == 7
    assert equipment.rows[0].category == '技术协议-按设备计'
    assert equipment.rows[1].category == '技术协议-按协议计'


@pytest.mark.parametrize('group_by', ['project', 'profession'])
def test_group_subtotals_reconcile_and_rates_use_total_counts(source, group_by):
    source.add('first')
    source.add('second', type_id='a2', project='p2', profession='f2', actual='2026-11-13')
    source.add('third', project='p2', profession='f2', actual='')
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(group_by=group_by))
    assert report.summaries.equipment.total == 3 and report.summaries.equipment.total_rate == 33.33
    tables = [table for table in report.tables if table.kind == 'equipment']
    assert len(tables) == 2
    assert [table.totals.total for table in tables] == [1, 2]
    assert [table.totals.total_rate for table in tables] == [100, 0]
    assert sum(table.totals.total for table in tables) == report.summaries.equipment.total
    for table in tables:
        category = [row for row in table.rows if row.category == '技术协议-按设备计']
        assert sum(row.total for row in category if not row.subtotal) == category[-1].total == table.totals.total
        assert category[-1].subtotal and category[-1].group_name == '总计'


@pytest.mark.parametrize('kinds, expected_count', [(['equipment'], 3), (['drawing'], 1), (['equipment', 'drawing'], 4)])
def test_summary_contains_every_document_template_without_extra_aggregate_queries(source, kinds, expected_count):
    source.add('equipment')
    source.add('drawing', type_id='c')
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=kinds))
    assert len(report.tables) == expected_count
    assert len({table.key for table in report.tables}) == expected_count
    assert len(source.sql) == 2 * len(kinds)
    if 'equipment' in kinds:
        assert report.summaries.equipment.total == 1
        assert sum(table.totals.total for table in report.tables if table.kind == 'equipment') == 3


@pytest.mark.parametrize('group_by, inner_label, expected_scopes', [
    ('project', '专业', ['项目一', '项目二']), ('profession', '项目', ['船体', '轮机'])])
def test_each_scope_gets_its_own_table_with_only_related_inner_groups(source, group_by, inner_label, expected_scopes):
    source.add('p1-f1')
    source.add('p1-f2', profession='f2', actual='2026-11-15')
    source.add('p2-f2', project='p2', profession='f2', actual='')
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['equipment'], group_by=group_by))
    assert [table.scope_label for table in report.tables] == expected_scopes
    assert all(table.group_label == inner_label for table in report.tables)
    assert report.summaries.equipment.total == 3 and report.summaries.equipment.total_rate == 33.33
    assert sum(table.totals.total for table in report.tables) == 3
    inner_names = [[row.group_name for row in table.rows if row.category == '技术协议-按设备计' and not row.subtotal] for table in report.tables]
    assert inner_names == ([['船体', '轮机'], ['轮机']] if group_by == 'project' else [['项目一'], ['项目一', '项目二']])


def test_scopes_and_empty_owner_selection_do_not_expand_results(source):
    source.add('first')
    source.add('second', project='p2', profession='f2')
    service = PdmStatisticsService(PdmStatisticsRepository(source))
    result = service.report(query(equipment={'projects': ['p1'], 'professions': ['f1'], 'levels': ['A'], 'areas': ['机舱'], 'manufactory': ['是'], 'responsible_people': ['甲']}))
    assert result.tables[0].totals.total == 1
    assert service.report(query(equipment={'responsible_people': []})).tables[0].totals.total == 0
    assert service.report(query(equipment={'projects': ["p1' OR 1=1 --"]})).tables[0].totals.total == 0
    assert all("p1' OR 1=1 --" not in sql for sql, _ in source.sql)
    assert service.report(query(equipment={'types': ['设备技术协议-按单协议统计']})).tables[0].totals.total == 0


def test_real_manufactory_encodings_share_filter_values_without_treating_missing_as_false(source):
    for ident, raw in [('boolean-yes', 'TRUE'), ('boolean-no', 'FALSE'), ('text-yes', '是'),
                       ('text-no', '否'), ('missing', None), ('unrecognized', 'NA')]:
        source.add(ident)
        source.db.execute('UPDATE PDM_DesignDocumentInfo SET Custom14=? WHERE ID=?', (raw, ident))
    service = PdmStatisticsService(PdmStatisticsRepository(source))
    for selection, expected in [(['是'], 2), (['否'], 2), (['是', '否'], 4), ([''], 1), (['NA'], 1), ([], 6)]:
        report = service.report(query(document_kinds=['equipment'], equipment={'manufactory': selection}))
        assert report.summaries.equipment.total == expected
    expression = PdmStatisticsRepository.fields('equipment')['manufactory']
    options_sql = []
    def option_rows(sql, parameters):
        sqlglot.parse_one(sql, read='tsql')
        options_sql.append(sql)
        return []
    source.fetch_all = option_rows
    service.options(PdmOptionsQuery(kind='equipment'))
    assert expression in options_sql[0]


def test_real_professions_and_letter_levels_are_kept_as_source_values(source):
    source.db.executemany('INSERT INTO PDM_DesignProfessional VALUES (?,?)', [('f3', '罐体结构'), ('f4', '罐体管装')])
    source.add('tank-structure', profession='f3')
    source.add('tank-piping', profession='f4')
    source.db.execute("UPDATE PDM_DesignDocumentInfo SET Custom12='B', Custom13='NA'")
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['equipment'], group_by='profession', equipment={'levels': ['B']}))
    assert {table.scope_label for table in report.tables} == {'罐体结构', '罐体管装'}
    assert report.summaries.equipment.total == 2


def test_completed_drawing_without_plan_is_on_time_and_uses_actual_date_scope(source):
    source.add('unplanned-completed', type_id='c', plan=None, actual='2026-11-04')
    source.add('planned-completed', type_id='c')
    source.add('unplanned-before-range', type_id='c', plan=None, actual='2026-10-31')
    source.add('unplanned-after-range', type_id='c', plan='', actual='2026-11-11')
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['drawing']))
    assert report.summaries.drawing.total == report.summaries.drawing.completed == report.summaries.drawing.on_time == 2


@pytest.mark.parametrize('start,end', [('2026-11-01', '2026-11-10'), ('2026-12-01', '2026-12-31')])
def test_both_dates_blank_are_unfinished_in_every_interval_and_still_respect_filters(source, start, end):
    source.add('null-dates', type_id='c', plan=None, actual=None)
    source.add('blank-dates', type_id='c', plan='  ', actual='')
    source.add('other-project', type_id='c', project='p2', plan=None, actual=None)
    source.add('abandoned', type_id='c', plan=None, actual=None, abandoned=1)
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['drawing'], start_date=start, end_date=end, drawing={'projects': ['p1']}))
    assert report.summaries.drawing.model_dump() == dict(total=2, completed=0, on_time=0, total_rate=0.0, on_time_rate=0.0)


def test_invalid_nonempty_dates_are_not_treated_as_blank_unplanned_documents(source):
    source.add('invalid-plan', type_id='c', plan='not-a-date', actual='2026-11-04')
    source.add('invalid-actual', type_id='c', plan=None, actual='not-a-date')
    source.add('both-invalid', type_id='c', plan='bad', actual='bad')
    result = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['drawing']))
    assert result.summaries.drawing.total == 0


def test_zero_denominator_and_query_validation(source):
    result = PdmStatisticsService(PdmStatisticsRepository(source)).report(query())
    assert result.tables[0].totals.total_rate is None
    for change in [dict(equipment={'types': ['完工文档']}), dict(end_date='2026-10-01'), dict(document_kinds=['drawing', 'drawing'])]:
        values = dict(document_kinds=['equipment'], start_date='2026-11-01', end_date='2026-11-10')
        values.update(change)
        with pytest.raises(ValidationError):
            PdmStatisticsQuery.model_validate(values)
    with pytest.raises(ValidationError):
        PdmOptionsQuery.model_validate({'kind': 'equipment', 'filters': {'types': ['预审图']}})


def test_profession_order_and_zero_rows_follow_document(source):
    source.db.execute("INSERT INTO PDM_DesignProfessional VALUES ('f3','总体')")
    source.add('first', profession='f3', plan='2026-10-01')
    source.add('second', profession='f1')
    source.add('third', profession='f2')
    tables = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(group_by='profession')).tables
    assert [table.scope_label for table in tables if table.kind == 'equipment'] == ['总体', '船体', '轮机']
    assert tables[0].totals.total == 0 and tables[0].totals.total_rate is None


def test_options_contract_scope_parameters_and_blank_owner_label(source):
    def values(sql, parameters):
        sqlglot.parse_one(sql, read='tsql')
        assert 'p2' in parameters and 'f2' in parameters
        assert sql.count('?') == len(parameters)
        return [dict(field='projects', value='p2', label='项目二 · S2'),
                dict(field='professions', value='f2', label='轮机'),
                dict(field='responsible_people', value='', label='')]
    source.fetch_all = values
    result = PdmStatisticsService(PdmStatisticsRepository(source)).options(PdmOptionsQuery.model_validate(dict(kind='equipment', filters=dict(projects=['p2'], professions=['f2']))))
    assert result.projects[0].value == 'p2'
    assert result.responsible_people[0].value == ''
    assert result.responsible_people[0].label == '未填写'


def test_empty_period_keeps_selected_project_and_profession_in_title(source):
    source.add('outside-period', plan='2026-10-01')
    table = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(equipment=dict(projects=['p1'], professions=['f1']))).tables[0]
    assert table.totals.total == 0
    assert table.scope_label == '项目一 / 船体'


def test_report_http_contract_role_validation_and_failure_redaction(source, monkeypatch):
    source.add('first')
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    monkeypatch.setattr(api, 'service', lambda request: PdmStatisticsService(PdmStatisticsRepository(source)))
    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'pdm-test'
        return await call_next(request)
    with TestClient(app) as client:
        response = client.post('/api/v1/pdm-statistics/report', json=query().model_dump(mode='json'))
        assert response.status_code == 200
        assert response.json()['data']['tables'][0]['totals']['total_rate'] == 100
        assert client.post('/api/v1/pdm-statistics/report', json={}).status_code == 422
        def fail(*args):
            raise RuntimeError('password=not-for-client')
        monkeypatch.setattr(source, 'fetch_all', fail)
        response = client.post('/api/v1/pdm-statistics/report', json=query().model_dump(mode='json'))
        assert response.status_code == 502 and 'not-for-client' not in response.text
        app.dependency_overrides.clear()
        app.dependency_overrides[require_user] = lambda: {'role': 'worker'}
        assert client.post('/api/v1/pdm-statistics/report', json=query().model_dump(mode='json')).status_code == 403


def test_disabled_source_does_not_fall_back_to_mock(monkeypatch):
    monkeypatch.setattr(api, 'runtime_state', lambda request: SimpleNamespace(settings=SimpleNamespace(file_database_enabled=False)))
    with pytest.raises(Exception) as error:
        api.service(None)
    assert error.value.status_code == 503


def test_main_mount_and_shared_source_identity(source, monkeypatch):
    from app import main
    from app.core.sql_capacity import current_sql_identity
    source.add('first')
    context = SimpleNamespace(settings=SimpleNamespace(file_source_key='pdm-source', dispatch_source_key='magic-source'))
    monkeypatch.setattr(main, 'runtime_state', lambda request: context)
    def current_service(request):
        assert current_sql_identity.get().source_key == 'pdm-source'
        return PdmStatisticsService(PdmStatisticsRepository(source))
    monkeypatch.setattr(api, 'service', current_service)
    monkeypatch.setattr(main.app, 'dependency_overrides', {require_admin: lambda: {'role': 'admin'}})
    # No lifespan: this isolated request must not build a production Runtime.
    client = TestClient(main.app)
    try:
        response = client.post('/api/v1/pdm-statistics/report', json=query().model_dump(mode='json'))
        assert response.status_code == 200
        assert response.json()['data']['tables'][0]['totals']['total'] == 1
    finally:
        client.close()
