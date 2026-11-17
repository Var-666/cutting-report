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


DOCUMENT_PROFESSIONS = ['总体', '外舾', '内装', '船体', '轮机', '电气', '船管', '工法']
DOCUMENT_CATEGORIES = {
    'equipment': ['技术协议-按设备计', '技术协议-按协议计', '设备认可图', '设备工作资料', '设备完工资料'],
    'drawing': ['预审图', '送审图', '船东退审图', '船检退审图', 'O版图', '详设下发图', '完工图'],
}


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


def test_overview_counts_both_kinds_across_all_dates_and_filters(source):
    source.add('current')
    source.add('old', project='p2', profession='f2', plan='2025-01-05', actual='2025-01-04')
    source.add('future', plan='2027-12-01', actual='2027-12-02')
    source.add('no-plan', plan=None, actual='2026-11-04')
    source.add('no-dates', plan=None, actual=None)
    source.add('invalid-actual', actual='bad')
    source.add('abandoned', abandoned=1)
    source.add('generic', type_id='e')
    source.add('drawing-complete', type_id='c', plan=None, actual='2026-11-04')
    source.add('drawing-unfinished', type_id='c', plan=None, actual=None)
    service = PdmStatisticsService(PdmStatisticsRepository(source))
    overview = service.overview()
    assert len(source.sql) == 2
    assert overview.equipment.model_dump() == dict(total=6, completed=4, on_time=3, total_rate=66.67, on_time_rate=50.0)
    assert overview.drawing.model_dump() == dict(total=2, completed=1, on_time=1, total_rate=50.0, on_time_rate=50.0)
    filtered = service.report(query(document_kinds=['equipment'], equipment={'projects': ['p1'], 'responsible_people': []}))
    assert filtered.summaries.equipment.total == 0 and filtered.summaries.drawing is None
    assert service.overview() == overview


def test_empty_overview_returns_both_kinds_without_nan_or_division_by_zero(source):
    overview = PdmStatisticsService(PdmStatisticsRepository(source)).overview()
    for counts in [overview.equipment, overview.drawing]:
        assert counts.model_dump() == dict(total=0, completed=0, on_time=0, total_rate=None, on_time_rate=None)


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
    assert inner_names == ([DOCUMENT_PROFESSIONS] * 2 if group_by == 'project' else [['项目一'], ['项目一', '项目二']])


@pytest.mark.parametrize('group_by', ['summary', 'project'])
def test_profession_rows_are_fixed_and_merge_names_without_losing_other_documents(source, group_by):
    source.db.executemany('INSERT INTO PDM_DesignProfessional VALUES (?,?)', [
        ('f3', '船体'), ('f4', '工法涂装'), ('f5', '机管'), ('f6', '罐体结构')])
    for ident, profession in [('first', 'f1'), ('same-name', 'f3'), ('paint', 'f4'),
                             ('piping', 'f5'), ('tank', 'f6'), ('missing', None)]:
        source.add(ident, profession=profession)
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['equipment'], group_by=group_by))
    table = next(table for table in report.tables if table.group_label == '专业')
    for category in {row.category for row in table.rows}:
        rows = [row for row in table.rows if row.category == category]
        assert [row.group_name for row in rows] == [*DOCUMENT_PROFESSIONS, '总计']
        assert len({row.group_id for row in rows}) == 9
    rows = table.rows[:9]
    assert rows[3].group_name == '船体' and rows[3].total == 2
    assert rows[7].group_name == '工法' and rows[7].total == 0
    assert rows[-1].total == table.totals.total == report.summaries.equipment.total == 6
    assert sum(row.total for row in rows if not row.subtotal) == 2


@pytest.mark.parametrize('group_by', ['summary', 'project', 'profession'])
@pytest.mark.parametrize('empty_period', [False, True])
def test_all_tables_keep_the_complete_document_templates_with_type_filters(source, group_by, empty_period):
    source.add('selected-equipment')
    source.add('excluded-equipment', type_id='b')
    source.add('selected-drawing', type_id='c')
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(
        group_by=group_by, start_date='2026-12-01' if empty_period else '2026-11-01', end_date='2026-12-31',
        equipment={'types': ['设备技术协议-按设备统计']}, drawing={'types': ['预审图']}))
    assert len(report.tables) == (4 if group_by == 'summary' else 2)
    for table in report.tables:
        assert list(dict.fromkeys(row.category for row in table.rows)) == DOCUMENT_CATEGORIES[table.kind]
        for category in DOCUMENT_CATEGORIES[table.kind]:
            rows = [row for row in table.rows if row.category == category]
            if table.group_label:
                names = DOCUMENT_PROFESSIONS if table.group_label == '专业' else ['项目一']
                assert [row.group_name for row in rows] == [*names, '总计']
                assert [row.subtotal for row in rows] == [False] * len(names) + [True]
            else:
                assert len(rows) == 1 and not rows[0].subtotal
            expected = int(not empty_period and category == DOCUMENT_CATEGORIES[table.kind][0])
            assert rows[-1].total == rows[-1].completed == rows[-1].on_time == expected
            assert rows[-1].total_rate == (100 if expected else None)
        assert table.totals.total == getattr(report.summaries, table.kind).total == int(not empty_period)


@pytest.mark.parametrize('kind,type_id', [('equipment', 'a'), ('drawing', 'c')])
def test_profession_partition_merges_project_specific_ids_and_omits_non_template_scopes(source, kind, type_id):
    source.db.executemany('INSERT INTO PDM_DesignProfessional VALUES (?,?)', [('f3', '船体'), ('f4', '罐体结构')])
    source.add('first', type_id=type_id)
    source.add('same-profession', type_id=type_id, project='p2', profession='f3')
    source.add('other-profession', type_id=type_id, profession='f4')
    source.add('missing-profession', type_id=type_id, profession=None)
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=[kind], group_by='profession'))
    assert [table.scope_label for table in report.tables] == ['船体']
    assert report.tables[0].totals.total == 2
    assert getattr(report.summaries, kind).total == 4
    assert [row.group_name for row in report.tables[0].rows[:3]] == ['项目一', '项目二', '总计']


@pytest.mark.parametrize('group_by', ['summary', 'project', 'profession'])
def test_missing_projects_do_not_generate_placeholder_rows_or_tables(source, group_by):
    source.add('valid')
    source.add('missing', project=None)
    source.add('orphan', project='deleted')
    source.db.execute("INSERT INTO BasicConfig_ProjectInfo VALUES ('p3','  ','S3')")
    source.add('blank-name', project='p3')
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['equipment'], group_by=group_by))
    assert report.summaries.equipment.total == 4
    assert all('未填写' not in table.scope_label for table in report.tables)
    assert all(row.group_name != '未填写' for table in report.tables for row in table.rows)
    if group_by == 'project':
        assert [table.scope_label for table in report.tables] == ['项目一']
        assert report.tables[0].totals.total == 1
    else:
        project_table = next(table for table in report.tables if table.group_label == '项目')
        assert [row.group_name for row in project_table.rows[:2]] == ['项目一', '总计']
        assert project_table.rows[0].total == 1 and project_table.rows[1].total == 4


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


def test_non_template_professions_keep_letter_level_filter_and_global_counts(source):
    source.db.executemany('INSERT INTO PDM_DesignProfessional VALUES (?,?)', [('f3', '罐体结构'), ('f4', '罐体管装')])
    source.add('tank-structure', profession='f3')
    source.add('tank-piping', profession='f4')
    source.db.execute("UPDATE PDM_DesignDocumentInfo SET Custom12='B', Custom13='NA'")
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['equipment'], group_by='profession', equipment={'levels': ['B']}))
    assert report.tables == []
    assert report.summaries.equipment.total == 2


def test_completed_drawing_without_plan_is_on_time_and_uses_actual_date_scope(source):
    source.add('unplanned-completed', type_id='c', plan=None, actual='2026-11-04')
    source.add('planned-completed', type_id='c')
    source.add('unplanned-before-range', type_id='c', plan=None, actual='2026-10-31')
    source.add('unplanned-after-range', type_id='c', plan='', actual='2026-11-11')
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['drawing']))
    assert report.summaries.drawing.total == report.summaries.drawing.completed == report.summaries.drawing.on_time == 2


@pytest.mark.parametrize('kind,type_id', [('equipment', 'a'), ('drawing', 'c')])
@pytest.mark.parametrize('date_basis,completed,on_time', [('plan', 2, 2), ('actual', 3, 1)])
def test_date_basis_switches_population_and_falls_back_only_for_blank_selected_dates(source, kind, type_id, date_basis, completed, on_time):
    source.add('actual-in-range', type_id=type_id, plan='2026-10-31', actual='2026-11-03')
    source.add('plan-in-range', type_id=type_id, plan='2026-11-05', actual='2026-10-30')
    source.add('missing-actual', type_id=type_id, plan='2026-11-05', actual=' ')
    source.add('missing-plan', type_id=type_id, plan=None, actual='2026-11-04')
    source.add('missing-both', type_id=type_id, plan=None, actual=None)
    source.add('invalid-actual', type_id=type_id, plan='2026-11-05', actual='bad')
    source.add('invalid-plan', type_id=type_id, plan='bad', actual='2026-11-04')
    service = PdmStatisticsService(PdmStatisticsRepository(source))
    report = service.report(query(document_kinds=[kind], date_basis=date_basis))
    assert report.date_basis == date_basis
    result = getattr(report.summaries, kind)
    assert (result.total, result.completed, result.on_time) == (5, completed, on_time)
    assert result.total_rate == completed / 5 * 100
    assert result.on_time_rate == on_time / 5 * 100
    empty_owners = service.report(query(document_kinds=[kind], date_basis=date_basis, **{kind: {'responsible_people': []}}))
    assert getattr(empty_owners.summaries, kind).total == 0


@pytest.mark.parametrize('start,end', [('2026-11-01', '2026-11-10'), ('2026-12-01', '2026-12-31')])
@pytest.mark.parametrize('date_basis', ['plan', 'actual'])
def test_both_dates_blank_are_unfinished_in_every_interval_and_still_respect_filters(source, start, end, date_basis):
    source.add('null-dates', type_id='c', plan=None, actual=None)
    source.add('blank-dates', type_id='c', plan='  ', actual='')
    source.add('other-project', type_id='c', project='p2', plan=None, actual=None)
    source.add('abandoned', type_id='c', plan=None, actual=None, abandoned=1)
    report = PdmStatisticsService(PdmStatisticsRepository(source)).report(query(document_kinds=['drawing'], start_date=start, end_date=end, date_basis=date_basis, drawing={'projects': ['p1']}))
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
    for change in [dict(equipment={'types': ['完工文档']}), dict(end_date='2026-10-01'), dict(document_kinds=['drawing', 'drawing']), dict(date_basis='created')]:
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


@pytest.mark.parametrize('kind', ['equipment', 'drawing'])
def test_project_options_include_engineering_projects_without_documents_and_ignore_document_scope(source, kind):
    source.db.executemany('INSERT INTO BasicConfig_ProjectInfo VALUES (?,?,?)', [
        ('p3', '尚无文档的项目', 'S3'), ('p4', '  ', 'S4'), (None, '无编号项目', 'S5')])
    source.add('existing')
    def project_rows(sql, parameters):
        assert sql.count('?') == len(parameters)
        parsed = sqlglot.parse_one(sql.replace('dbo.', ''), read='tsql')
        assert isinstance(parsed, sqlglot.exp.Union)
        project_branch = parsed.expression.copy().transform(
            lambda node: sqlglot.exp.Literal.string(node.this) if isinstance(node, sqlglot.exp.National) else node)
        # SQL Server uses + for text concatenation; SQLite requires ||.
        for addition in list(project_branch.find_all(sqlglot.exp.Add)):
            addition.replace(sqlglot.exp.DPipe(this=addition.this, expression=addition.expression))
        assert not list(project_branch.find_all(sqlglot.exp.Placeholder))
        return [dict(zip(['field', 'value', 'label'], row, strict=True))
                for row in source.db.execute(project_branch.sql(dialect='sqlite'))]
    source.fetch_all = project_rows
    service = PdmStatisticsService(PdmStatisticsRepository(source))
    for filters in [{}, {'projects': ['p1'], 'professions': ['f1']}, {'projects': ['p3']}]:
        options = service.options(PdmOptionsQuery(kind=kind, filters=filters))
        assert {option.value: option.label for option in options.projects} == {
            'p1': '项目一 · S1', 'p2': '项目二 · S2', 'p3': '尚无文档的项目 · S3'}


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
        assert response.json()['data']['date_basis'] == 'plan'
        actual = client.post('/api/v1/pdm-statistics/report', json=query(date_basis='actual').model_dump(mode='json'))
        assert actual.status_code == 200 and actual.json()['data']['date_basis'] == 'actual'
        response = client.get('/api/v1/pdm-statistics/overview')
        assert response.status_code == 200
        assert response.json()['data']['equipment']['total_rate'] == 100
        assert response.json()['data']['drawing']['total'] == 0
        assert client.post('/api/v1/pdm-statistics/report', json={}).status_code == 422
        def fail(*args):
            raise RuntimeError('password=not-for-client')
        monkeypatch.setattr(source, 'fetch_all', fail)
        response = client.post('/api/v1/pdm-statistics/report', json=query().model_dump(mode='json'))
        assert response.status_code == 502 and 'not-for-client' not in response.text
        response = client.get('/api/v1/pdm-statistics/overview')
        assert response.status_code == 502 and 'not-for-client' not in response.text
        app.dependency_overrides.clear()
        app.dependency_overrides[require_user] = lambda: {'role': 'worker'}
        assert client.post('/api/v1/pdm-statistics/report', json=query().model_dump(mode='json')).status_code == 403
        assert client.get('/api/v1/pdm-statistics/overview').status_code == 403


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
