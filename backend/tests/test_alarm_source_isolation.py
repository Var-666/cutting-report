from datetime import datetime
from zoneinfo import ZoneInfo

from app.database.alarms import AlarmRepository, _fingerprint
from app.database.sqlite import SQLiteDatabase
from app.database.migrations import apply_migrations
from app.services.application_runtime import build_runtime
from app.services.database_runtime import database_context
from tests.line_fixtures import configuration

STAMP = '2026-11-01T08:00:00+08:00'
NOW = datetime.fromisoformat(STAMP)


def record(machine='Cutter01'):
    return dict(alarm_info_id=1, machine_no=machine, alarm_type='PLC', alarm_code='A01', alarm_class='fault',
                alarm_desc='故障', start_time=STAMP, remove_time=None)


def test_external_event_ids_and_open_incidents_are_isolated_by_source(tmp_path):
    db = SQLiteDatabase(tmp_path / 'metadata.db')
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
        assert connection.execute('SELECT COUNT(*) FROM alarm_source_events').fetchone()[0] == 3
        assert connection.execute('SELECT COUNT(*) FROM alarm_incidents').fetchone()[0] == 3
    closed = record()
    closed['remove_time'] = '2026-11-01T09:00:00+08:00'
    switched.ingest([closed], cursor=1, now=NOW, debounce_minutes=10)
    assert switched.list_open_source_ids() == [] and one.list_open_source_ids() == [1]


def test_v015_upgrade_preserves_incidents_events_and_cursor_without_duplicate_reports(tmp_path):
    cfg = configuration(tmp_path)
    db = SQLiteDatabase(cfg.metadata_db_path)
    apply_migrations(db, names=('012_alarms',))
    with db.connect() as connection:
        connection.execute('''INSERT INTO alarm_incidents
            (incident_id,fingerprint,machine_no,alarm_type,alarm_code,alarm_class,alarm_desc,severity,status,
             first_seen_at,last_seen_at,first_start_time,first_alarm_id,latest_alarm_id)
            VALUES ('old',?,'Cutter01','PLC','A01','fault','故障','high','open',?,?,?,1,1)''', (_fingerprint(record()), STAMP, STAMP, STAMP))
        connection.execute('''INSERT INTO alarm_source_events
            (source_alarm_id,incident_id,source_hash,start_time,is_open,first_seen_at,last_seen_at)
            VALUES (1,'old','old-hash',?,1,?,?)''', (STAMP, STAMP, STAMP))
        connection.execute("INSERT INTO alarm_sync_state(source_name,last_alarm_id) VALUES ('production:Cutter01',1)")
    runtime = build_runtime(cfg, role='web')
    repository = database_context(runtime).lines['Cutter01'].cutting().alarm_repository
    assert repository.get_cursor() == 1 and repository.list_open_source_ids() == [1]
    repository.ingest([record()], cursor=1, now=NOW, debounce_minutes=10)
    with db.connect() as connection:
        assert connection.execute('SELECT incident_id FROM alarm_incidents').fetchall()[0][0] == 'old'
        assert connection.execute('SELECT COUNT(*) FROM alarm_incidents').fetchone()[0] == 1
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    restarted = build_runtime(cfg, role='worker')
    assert database_context(restarted).lines['Cutter01'].cutting().alarm_repository.get_cursor() == 1
