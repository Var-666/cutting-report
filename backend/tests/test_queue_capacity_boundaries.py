"""Existing 64 active snapshots and 256 pending tasks, without changing policy."""
from app.database.coordination import CoordinationRepository
from app.database.postgres import execution_scope
from app.scheduler import SchedulerProcess


def test_snapshot_limit_rejection_has_no_row_and_scheduler_retries_after_capacity_returns(postgres_state):
    database, cfg, _ = postgres_state
    queue = CoordinationRepository(database)
    payload = {'report_type':'page:daily','query':{'start':'2026-11-04','end':'2026-11-04'},'source_fingerprint':'test-source'}
    for i in range(64):
        assert queue.request_snapshot_refresh('welding', {**payload,'query':{'index':i}})
    scheduler = SchedulerProcess(cfg)
    owner = queue.acquire('worker:capacity-test',ttl=30)
    try:
        scheduler.start()
        with scheduler.scope():
            scheduler._schedule_snapshot('welding',payload,30)
        assert not queue.snapshot_refresh_active('welding',payload)
        with database.connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM background_tasks').fetchone()['n']==64
        with execution_scope(database,'worker:capacity-test',owner):
            task = queue.claim_task(owner,analysis=False)
            assert queue.finish_task(task,owner)
        with scheduler.scope():
            scheduler._schedule_snapshot('welding',payload,30)
        assert queue.snapshot_refresh_active('welding',payload)
        with database.connect() as conn:
            assert conn.execute("SELECT count(*) AS n FROM background_tasks WHERE status IN ('pending','running','deferred')").fetchone()['n']==64
            assert conn.execute("SELECT count(*) AS n FROM background_tasks WHERE status='completed'").fetchone()['n']==1
    finally:
        scheduler.stop()


def test_pending_limit_persists_overflow_and_admits_deferred_when_a_slot_is_released(postgres_state):
    database, _, _ = postgres_state
    queue = CoordinationRepository(database)
    for i in range(257):
        assert queue.enqueue('analysis_test','Cutter01',{},key=f'capacity-{i}')
    assert queue.task_counts()=={'pending':256,'deferred':1}
    queue.admit_deferred()
    assert queue.task_counts()=={'pending':256,'deferred':1}
    owner=queue.acquire('worker:capacity-test',ttl=30)
    with execution_scope(database,'worker:capacity-test',owner):
        task=queue.claim_task(owner,analysis=True)
        assert queue.finish_task(task,owner)
        queue.admit_deferred()
    assert queue.task_counts()=={'pending':256,'completed':1}
    payload={'report_type':'page:daily','query':{},'source_fingerprint':'test-source'}
    assert queue.request_snapshot_refresh('welding',payload)
    with database.connect() as conn:
        row=conn.execute('SELECT status FROM background_tasks WHERE dedup_key=%s',
                         (queue.snapshot_task_key('welding',payload),)).fetchone()
        assert row['status']=='deferred', 'below 64 active snapshots, the shared 256 pending threshold still applies'
