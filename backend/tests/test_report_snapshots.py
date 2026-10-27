from dataclasses import replace
from datetime import datetime, timedelta, timezone
import multiprocessing as mp
from pathlib import Path

import pytest

from app.config import Settings
from app.database.coordination import CoordinationRepository
from app.database.report_snapshots import ReportKey, ReportSnapshot, ReportSnapshotRepository, fingerprint
from app.database.sqlite import SQLiteDatabase, ExecutionLeaseLost, execution_scope
from app.services.report_snapshots import ReportSnapshotService


def infrastructure(root):
    settings = Settings(project_root=Path(root), device_id='welding')
    database = SQLiteDatabase(settings.metadata_db_path)
    coordination = CoordinationRepository(database)
    coordination.initialize()
    repository = ReportSnapshotRepository(database)
    repository.initialize()
    return settings, repository, coordination


def key(**changes):
    return replace(ReportKey('welding', 'statistics', '2026-10-01', '2026-10-27', {'device_no': 0}, 'source'), **changes)


def record(report_key=None, **changes):
    now = datetime.now(timezone.utc)
    payload = {'count': 3}
    return replace(ReportSnapshot(report_key or key(), payload, fingerprint(payload), now, now, now + timedelta(seconds=300)), **changes)


def test_snapshot_is_additive_atomic_versioned_and_bounded(tmp_path):
    settings, repo, _ = infrastructure(tmp_path)
    with repo.connect() as connection:
        connection.execute('CREATE TABLE legacy_data (value TEXT)')
        connection.execute("INSERT INTO legacy_data VALUES ('keep')")
    first = record()
    repo.save(first)
    assert repo.get(key()).payload == {'count': 3}
    assert ReportSnapshotRepository(repo.database).get(key()).data_as_of == first.data_as_of
    with pytest.raises(ValueError, match='版本'):
        repo.save(replace(first, payload={'count': 4}))
    assert repo.get(key()).payload == {'count': 3}
    with repo.connect() as connection:
        connection.execute("UPDATE report_snapshots SET payload='{}'")
    assert repo.get(key()) is None
    for i in range(130):
        repo.save(record(key(filters={'device_no': i})))
    with repo.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM report_snapshots').fetchone()[0] == 128
        assert connection.execute('SELECT value FROM legacy_data').fetchone()[0] == 'keep'


@pytest.mark.parametrize('changes', [
    {'line_id': 'Cutter01'}, {'report_type': 'parts'}, {'period_end': '2026-10-26'},
    {'filters': {'device_no': 1}}, {'source_fingerprint': 'other'}, {'schema_version': 2}, {'access_scope': 'worker:7'},
])
def test_snapshot_key_isolates_every_data_boundary(tmp_path, changes):
    _, repo, _ = infrastructure(tmp_path)
    repo.save(record())
    assert repo.get(key(**changes)) is None


def test_l1_restart_revision_force_and_stale_policy(tmp_path):
    settings, repo, coordination = infrastructure(tmp_path)
    service = ReportSnapshotService(settings, repo, coordination)
    calls = []
    def load():
        calls.append(1)
        return {'count': len(calls)}
    assert service.read(key(), load, ttl=300).meta['source'] == 'database'
    assert service.read(key(), load, ttl=300).meta['source'] == 'snapshot'
    assert service.read(key(), load, ttl=300).meta['source'] == 'cache'
    restarted = ReportSnapshotService(settings, repo, coordination)
    assert restarted.read(key(), load, ttl=300).payload == {'count': 1}
    assert len(calls) == 1
    assert restarted.read(key(), load, ttl=300, force=True).payload == {'count': 2}
    assert service.read(key(), load, ttl=300).payload == {'count': 2}
    old = repo.get(key())
    repo.save(replace(old, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    service.revision.publish()
    stale = service.read(key(), lambda: pytest.fail('expired requests must enqueue once'), ttl=300)
    assert stale.meta['stale'] and stale.meta['refreshing'] and stale.meta['data_as_of']
    for _ in range(3):
        service.read(key(), load, ttl=300)
    assert coordination.task_counts() == {'pending': 1}
    def fail():
        raise ConnectionError('source disconnected')
    fallback = service.read(key(), fail, ttl=300, force=True)
    assert fallback.meta['stale'] and fallback.meta['refresh_failed']
    assert repo.get(key()).data_version == old.data_version
    with pytest.raises(ConnectionError):
        service.read(key(), fail, ttl=300, force=True, background=True)
    repo.save(replace(old, expires_at=datetime.now(timezone.utc) - timedelta(days=2)))
    service.revision.publish()
    with pytest.raises(ConnectionError):
        service.read(key(), fail, ttl=300)


def _concurrent_read(root, start, entered, release, counter, output, force=False):
    settings, repository, _ = infrastructure(root)
    service = ReportSnapshotService(settings, repository)
    def load():
        with counter.get_lock():
            counter.value += 1
        entered.set()
        assert release.wait(10)
        return {'count': 7}
    output.put('ready')
    assert start.wait(10)
    output.put(service.read(key(), load, ttl=300, force=force).payload)


@pytest.mark.parametrize('force', [False, True])
def test_process_singleflight_shares_one_source_query(tmp_path, force):
    infrastructure(tmp_path)
    context = mp.get_context('spawn')
    start, entered, release = context.Event(), context.Event(), context.Event()
    counter, output = context.Value('i', 0), context.Queue()
    processes = [context.Process(target=_concurrent_read, args=(str(tmp_path), start, entered, release, counter, output, force)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        assert output.get(timeout=10) == output.get(timeout=10) == 'ready'
        start.set()
        assert entered.wait(10)
        release.set()
        assert output.get(timeout=10) == output.get(timeout=10) == {'count': 7}
        assert counter.value == 1
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
    finally:
        release.set()
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(10)


def test_worker_lease_loss_cannot_publish_and_queue_has_retry_dedup_and_limits(tmp_path):
    _, repo, coordination = infrastructure(tmp_path)
    payload = ReportSnapshotService.task_payload(key())
    assert coordination.request_snapshot_refresh('welding', payload)
    assert not coordination.request_snapshot_refresh('welding', payload)
    owner = coordination.acquire('worker', ttl=30)
    task = coordination.claim_task(owner, analysis=False)
    assert task['device_id'] == 'welding' and task['kind'] == 'report_snapshot_refresh'
    coordination.finish_task(task, owner, 'ConnectionError')
    with coordination.connect() as connection:
        row = connection.execute('SELECT * FROM background_tasks').fetchone()
        assert row['status'] == 'deferred' and row['next_attempt'] > row['updated_at']
    assert not coordination.request_snapshot_refresh('welding', payload)
    for i in range(70):
        coordination.request_snapshot_refresh('welding', {**payload, 'query': {'start': str(i)}})
    with coordination.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='report_snapshot_refresh'").fetchone()[0] == 64
        connection.execute("UPDATE process_leases SET expires_at=0 WHERE name='worker'")
    with pytest.raises(ExecutionLeaseLost), execution_scope(repo.database.path, 'worker', owner):
        repo.save(record())
    assert repo.get(key()) is None


def _refresh_in_another_process(root):
    settings, repository, _ = infrastructure(root)
    service = ReportSnapshotService(settings, repository)
    service.read(key(), lambda: {'count': 9}, ttl=300, force=True)


def test_worker_publication_invalidates_a_warm_web_l1_across_processes(tmp_path):
    settings, repository, _ = infrastructure(tmp_path)
    web = ReportSnapshotService(settings, repository)
    web.read(key(), lambda: {'count': 1}, ttl=300)
    web.read(key(), lambda: pytest.fail('source'), ttl=300)
    assert web.read(key(), lambda: pytest.fail('source'), ttl=300).meta['source'] == 'cache'
    context = mp.get_context('spawn')
    worker = context.Process(target=_refresh_in_another_process, args=(str(tmp_path),))
    worker.start()
    worker.join(10)
    try:
        assert worker.exitcode == 0
        result = web.read(key(), lambda: pytest.fail('Web must use the Worker snapshot'), ttl=300)
        assert result.payload == {'count': 9} and result.meta['source'] == 'snapshot'
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(10)


def test_background_read_refreshes_expired_snapshot_without_queuing_itself(tmp_path):
    settings, repository, coordination = infrastructure(tmp_path)
    repository.save(record(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    service = ReportSnapshotService(settings, repository, coordination)
    result = service.read(key(), lambda: {'count': 8}, ttl=300, background=True)
    assert result.payload == {'count': 8} and not result.meta['stale']
    assert coordination.task_counts() == {}
