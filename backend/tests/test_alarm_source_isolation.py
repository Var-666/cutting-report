from datetime import datetime
from zoneinfo import ZoneInfo

from app.database.alarms import AlarmRepository, _fingerprint

STAMP = '2026-11-01T08:00:00+08:00'
NOW = datetime.fromisoformat(STAMP)


def record(machine='Cutter01'):
    return dict(alarm_info_id=1, machine_no=machine, alarm_type='PLC', alarm_code='A01', alarm_class='fault',
                alarm_desc='故障', start_time=STAMP, remove_time=None)


def test_external_event_ids_and_open_incidents_are_isolated_by_source(state_database):
    db = state_database
    one = AlarmRepository(db, machine_no='Cutter01', source_name='source-1:Cutter01')
    other = AlarmRepository(db, machine_no='Cutter02', source_name='source-2:Cutter02')
    one.initialize()
    other.initialize()
    for repo, event in ((one, record()), (other, record('Cutter02'))):
        repo.ingest([event], cursor=1, now=NOW, debounce_minutes=10)
    assert one.list_open_source_ids() == other.list_open_source_ids() == [1]
    switched = AlarmRepository(db, machine_no='Cutter01', source_name='new-database:Cutter01')
    switched.initialize()
    assert switched.list_open_source_ids() == []
    switched.ingest([record()], cursor=1, now=NOW, debounce_minutes=10)
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) AS n FROM alarm_source_events').fetchone()['n'] == 3
        assert connection.execute('SELECT COUNT(*) AS n FROM alarm_incidents').fetchone()['n'] == 3
    closed = record()
    closed['remove_time'] = '2026-11-01T09:00:00+08:00'
    switched.ingest([closed], cursor=1, now=NOW, debounce_minutes=10)
    assert switched.list_open_source_ids() == [] and one.list_open_source_ids() == [1]
