from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.database.cutting_queries import CuttingQueryRepository
from app.database.workshop import WorkshopRepository
from app.database.workshop_errors import DailyReportSourceMismatchError, DailyReportRecordMismatchError
from app.models.domain import WorkshopRecord


def test_source_completion_lookup_is_read_only_bounded_and_ignores_invalid_end_dates():
    ids = [str(i) for i in range(502)]
    client = Mock()
    client.fetch_all.side_effect = [
        [{'task_id': '0', 'production_date': datetime(2026, 10, 1)}, {'task_id': '1', 'production_date': None}],
        [{'task_id': '501', 'production_date': '2026-10-02'}],
    ]
    dates = CuttingQueryRepository(client, 'Cutter01').task_completion_dates(ids)
    assert dates == {'0': date(2026, 10, 1), '501': date(2026, 10, 2)}
    assert client.fetch_all.call_count == 2
    for query, parameters in (call.args for call in client.fetch_all.call_args_list):
        assert query.lstrip().startswith('SELECT') and 'END_TIME' in query
        assert len(parameters) <= 501 and parameters[0] == 'Cutter01'


def test_source_completion_lookup_rejects_conflicting_task_dates():
    client = SimpleNamespace(fetch_all=lambda *_: [
        {'task_id': 'A', 'production_date': '2026-10-01'}, {'task_id': 'A', 'production_date': '2026-10-02'}])
    with pytest.raises(ValueError, match='冲突'):
        CuttingQueryRepository(client, 'Cutter01').task_completion_dates(['A'])


def snapshot(task_id, day):
    return WorkshopRecord(task_id, 'P', '船名', 'S', 'program', Decimal(10), Decimal(1), Decimal(90),
        production_time=datetime(2026, 9, 30, 23, 59), production_date=date.fromisoformat(day))


@pytest.fixture
def reports(state_database):
    repo = WorkshopRepository(state_database, scope_machine_no='Cutter01')
    user = repo.bootstrap_admin(username='completion-test', display_name='工人', password='test-password', now='2026-10-01')
    def create(day, records, source='sqlserver'):
        return repo.create_daily_report(report_date=day, source_mode=source, records=records, user=user, now='2026-10-01')
    first = create('2026-09-30', [snapshot('A', '2026-09-30'), snapshot('B', '2026-09-30')])
    repo.confirm_task(task_id='A', report_id=first['report_id'], user=user, now='2026-10-01T00:03:00')
    return repo, user, first, create


def test_history_reconciliation_previews_then_moves_and_merges_without_losing_confirmation(reports):
    repo, user, first, create = reports
    target = create('2026-10-01', [snapshot('A', '2026-10-01'), snapshot('C', '2026-10-01')])
    other = WorkshopRepository(repo.database, scope_machine_no='Cutter02')
    other.create_daily_report(report_date='2026-09-30', source_mode='sqlserver', records=[snapshot('A', '2026-09-30')], user=user, now='2026-10-01')
    before_confirmation = repo.get_confirmations(['A'])['A']
    before_audits = repo.list_confirmation_audits(task_id='A')
    old_item = repo.get_daily_report_records('2026-09-30')[1][0]
    dates = {'A': date(2026, 10, 1)}
    assert repo.reconcile_completion_dates(dates) == {'changed_tasks': 1, 'merged_snapshots': 1}
    assert repo.get_daily_report('2026-09-30')['item_count'] == 2
    assert repo.reconcile_completion_dates(dates, apply=True) == {'changed_tasks': 1, 'merged_snapshots': 1}
    assert repo.get_daily_report('2026-09-30')['item_count'] == 1
    report, records = repo.get_daily_report_records('2026-10-01')
    assert report['report_id'] == target['report_id'] and report['item_count'] == 2 and report['confirmed_count'] == 1
    moved = next(row for row in records if row.task_id == 'A')
    assert moved.production_date == date(2026, 10, 1) and moved.production_time == old_item.production_time
    assert moved.work_time == old_item.work_time and moved.snapshot_revision > old_item.snapshot_revision
    assert repo.get_confirmations(['A'])['A'] == {**before_confirmation, 'report_id': target['report_id']}
    assert repo.list_confirmation_audits(task_id='A') == before_audits
    assert other.get_daily_report('2026-09-30')['item_count'] == 1
    assert repo.reconcile_completion_dates(dates, apply=True) == {'changed_tasks': 0, 'merged_snapshots': 0}


def test_history_reconciliation_creates_destination_and_keeps_original_item_identity(reports):
    repo, _, first, _ = reports
    with repo.connect() as connection:
        item_id = connection.execute("SELECT report_item_id FROM workshop_report_items WHERE task_id='A'").fetchone()['report_item_id']
    repo.reconcile_completion_dates({'A': date(2027, 1, 1)}, apply=True)
    target, records = repo.get_daily_report_records('2027-01-01')
    assert target['confirmed_count'] == 1 and records[0].confirmed_by
    assert repo.get_daily_report('2026-09-30')['item_count'] == 1
    with repo.connect() as connection:
        assert connection.execute('SELECT report_id FROM workshop_report_items WHERE report_item_id=%s', (item_id,)).fetchone()['report_id'] == target['report_id']


def test_conflicting_destination_rolls_back_the_whole_reconciliation(reports):
    repo, _, _, create = reports
    create('2026-10-02', [snapshot('demo', '2026-10-02')], source='demo')
    with pytest.raises(DailyReportSourceMismatchError):
        repo.reconcile_completion_dates({'A': date(2026, 10, 1), 'B': date(2026, 10, 2)}, apply=True)
    assert repo.get_daily_report('2026-09-30')['item_count'] == 2
    assert repo.get_daily_report('2026-10-01') is None


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('field,value', [('work_time', Decimal(150)), ('project_code', 'new-project'),
                                      ('remark', 'new-note'), ('ship_no', 'new-ship')])
def test_conflicting_snapshot_values_are_preserved_and_rollback_other_moves(reports, apply, field, value):
    from dataclasses import replace
    repo, _, _, create = reports
    create('2026-10-01', [replace(snapshot('B', '2026-10-01'), **{field: value})])
    confirmations = repo.get_confirmations(['A'])
    audits = repo.list_confirmation_audits(task_id='A')
    with pytest.raises(DailyReportRecordMismatchError, match='生产字段不一致'):
        repo.reconcile_completion_dates({'A': date(2027, 1, 1), 'B': date(2026, 10, 1)}, apply=apply)
    assert repo.get_daily_report('2027-01-01') is None
    assert repo.get_daily_report('2026-09-30')['item_count'] == 2
    assert getattr(repo.get_daily_report_records('2026-10-01')[1][0], field) == value
    assert repo.get_confirmations(['A']) == confirmations
    assert repo.list_confirmation_audits(task_id='A') == audits


def test_command_previews_then_applies_and_reports_unresolved_tasks(reports, postgres_state, tmp_path, monkeypatch, capsys):
    import importlib.util
    import json
    from pathlib import Path
    from tests.line_fixtures import configuration
    from tests.postgres_fixtures import state_settings
    script = Path(__file__).resolve().parents[2] / 'scripts/reconcile_cutting_completion_dates.py'
    spec = importlib.util.spec_from_file_location('completion_date_command', script)
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    monkeypatch.setattr(command, 'settings', state_settings(configuration(tmp_path), postgres_state))
    client = SimpleNamespace(fetch_all=lambda *_: [{'task_id': 'A', 'production_date': '2026-10-01'}])
    registry = SimpleNamespace(get=lambda _: SimpleNamespace(engine='sqlserver'), client=lambda _: client, close=lambda: None)
    monkeypatch.setattr(command.DatabaseSourceRegistry, 'from_json', lambda _: registry)
    repo = reports[0]
    assert command.main(['--line', 'Cutter01']) == 2
    preview = json.loads(capsys.readouterr().out)
    assert preview['applied'] is False and preview['changed_tasks'] == 1 and preview['unresolved_tasks'] == 1
    assert repo.get_daily_report('2026-09-30')['item_count'] == 2
    assert command.main(['--line', 'Cutter01', '--apply']) == 2
    applied = json.loads(capsys.readouterr().out)
    assert applied['applied'] is True and applied['unresolved_sample'] == ['B']
    assert repo.get_daily_report('2026-10-01')['confirmed_count'] == 1
    assert repo.get_daily_report('2026-09-30')['item_count'] == 1
    from dataclasses import replace
    reports[3]('2026-10-02', [replace(snapshot('B', '2026-10-02'), work_time=Decimal(150))])
    client.fetch_all = lambda *_: [{'task_id': 'A', 'production_date': '2026-10-01'},
                                   {'task_id': 'B', 'production_date': '2026-10-02'}]
    assert command.main(['--line', 'Cutter01', '--apply']) == 1
    assert '任务 B 的历史生产字段不一致' in capsys.readouterr().err
    assert repo.get_daily_report_records('2026-10-02')[1][0].work_time == Decimal(150)
