"""P6 role boundaries and lifecycle checks against isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
import multiprocessing as mp
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from zoneinfo import ZoneInfo

import pytest

from app.scheduler import SchedulerProcess
from app.worker import WorkerProcess
from app.automation_worker import AutomationProcess
from app.database.coordination import CoordinationRepository
from app.database.postgres import execution_scope, ExecutionLeaseLost
from app.services.application_runtime import build_runtime
from app.services.worker_status import WorkerStatusReader, background_ready, public_status
from tests.postgres_fixtures import state_settings
from tests.line_fixtures import configuration

CLASSES={'scheduler':SchedulerProcess,'worker':WorkerProcess,'automation':AutomationProcess}


@pytest.mark.parametrize('role', ['scheduler', 'automation'])
def test_diagnostic_failure_does_not_block_role_heartbeat(postgres_state, monkeypatch, role):
    process = CLASSES[role](postgres_state[1])
    def unavailable(*args):
        raise RuntimeError('diagnostics unavailable')
    monkeypatch.setattr(process.runtime.configuration_diagnostics, 'observed', unavailable)
    try:
        process.start()
        status = process.repository.worker_status(role)
        assert status['state'] == 'running'
        assert status['configuration'] == {'error': '配置快照暂不可用'}
        assert not background_ready({'report_alive': True, 'state': 'running'})
    finally:
        process.stop()


def wait_until(check,seconds=8):
    deadline=monotonic()+seconds
    while not check():
        assert monotonic()<deadline,'role state did not settle'
        sleep(.03)


def _run_role(cfg, role, stopped, channel):
    process=CLASSES[role](cfg)
    process.stop_event=stopped
    try:
        process.start()
        channel.send(process.owner)
        while not stopped.wait(.05):
            if role=='automation': process._heartbeat()
            else: process.tick()
    finally:
        process.stop();channel.close()


@pytest.mark.parametrize('order',[('scheduler','worker','automation'),('worker','scheduler','automation'),('automation','worker','scheduler')])
def test_roles_start_independently_and_each_recovers_after_process_death(postgres_state,tmp_path,order):
    database,cfg,_=postgres_state
    cfg=replace(cfg,worker_poll_seconds=.05)
    queue=CoordinationRepository(database)
    context=mp.get_context('spawn')
    processes={};stops={};channels={}
    def start(role):
        stop=context.Event();parent,child=context.Pipe()
        process=context.Process(target=_run_role,args=(cfg,role,stop,child))
        process.start()
        processes[role]=process;stops[role]=stop;channels[role]=(parent,child)
        assert parent.poll(15)
        return parent.recv()
    try:
        owners={role:start(role) for role in order}
        wait_until(lambda:background_ready(WorkerStatusReader(database).status()))
        for role in order:
            assert queue.acquire(role,ttl=30) is None
        # Crash each role with the others still alive; takeover only after persisted expiry.
        for role in order:
            processes[role].kill();processes[role].join(5)
            assert queue.acquire(role,ttl=30) is None
            with database.connect() as connection:
                connection.execute('UPDATE process_leases SET expires_at=0 WHERE name=%s',(role,))
            assert queue.worker_status(role)['state']=='stopped'
            assert all(processes[other].is_alive() for other in order if other!=role)
            assert start(role)!=owners[role]
            wait_until(lambda:background_ready(WorkerStatusReader(database).status()))
        status=WorkerStatusReader(database).status()
        assert set(status['roles'])==set(CLASSES)
        assert all('configuration' not in s for s in public_status(status)['roles'].values())
    finally:
        for stop in stops.values(): stop.set()
        for process in processes.values():
            process.join(8)
            if process.is_alive(): process.kill();process.join(5)
        for parent,child in channels.values(): parent.close();child.close()


@pytest.mark.parametrize('role',list(CLASSES))
def test_role_fencing_rolls_back_expired_commits_and_rejects_foreign_recovery(postgres_state,role):
    database,_,_=postgres_state
    queue=CoordinationRepository(database)
    owner=queue.acquire(role,ttl=.1)
    with pytest.raises(ExecutionLeaseLost):
        with execution_scope(database,role,owner),database.connect() as connection:
            connection.execute('INSERT INTO llm_waiters VALUES(%s,0)',(role,))
            sleep(.15)
    with database.connect() as connection:
        assert connection.execute('SELECT 1 FROM llm_waiters WHERE waiter_id=%s',(role,)).fetchone() is None
    new=queue.acquire(role,ttl=30)
    assert new and new!=owner
    runtime=build_runtime(postgres_state[1],role=role)
    try:
        with execution_scope(database,role,new):
            if role=='automation':
                runtime.automation.recover_interrupted_deliveries()
                with pytest.raises(ExecutionLeaseLost):runtime.metadata.recover_interrupted_jobs()
            else:
                with pytest.raises(ExecutionLeaseLost):runtime.automation.recover_interrupted_deliveries()
    finally:runtime.database_runtime.close();runtime.database.close()


def test_scheduler_only_enqueues_consumer_only_executes_and_old_source_never_refreshes(postgres_state,tmp_path,monkeypatch):
    cfg=state_settings(configuration(tmp_path,real=True,welding=True),postgres_state)
    scheduler=SchedulerProcess(cfg);consumer=WorkerProcess(cfg)
    try:
        # Source execution doubles must never be called by scheduler.
        for line in scheduler.runtime.database_runtime.snapshot().lines.values():
            if line.background:
                monkeypatch.setattr(line.background,'handle_background',lambda *a:pytest.fail('scheduler executed business'))
        scheduler.owner=scheduler.repository.acquire('scheduler',ttl=30)
        with scheduler.scope():scheduler._schedule()
        assert scheduler.repository.task_counts()['pending']>0
        assert not hasattr(scheduler,'operations') and not hasattr(consumer,'_schedule')
        consumer.owner=consumer.repository.acquire('worker',ttl=30)
        before=consumer.repository.task_counts()
        task=consumer.repository.claim_task(consumer.owner,analysis=False)
        line=consumer.runtime.database_runtime.snapshot().lines[task['device_id']]
        seen=[]
        monkeypatch.setattr(line.background,'handle_background',lambda kind,payload:seen.append(kind))
        with consumer.scope():consumer._execute(task);consumer._flush_completions()
        assert seen==[task['kind']]
        with consumer.repository.connect() as connection:
            assert connection.execute("SELECT count(*) FROM background_tasks").fetchone()['count']==sum(before.values())
        provider=consumer.runtime.database_runtime.snapshot().lines['welding'].report_provider
        monkeypatch.setattr(provider,'refresh_summary',lambda *a,**kw:pytest.fail('stale source refreshed'))
        consumer._handle(dict(kind='report_snapshot_refresh',device_id='welding',payload={'source_fingerprint':'old-source'}))
    finally:scheduler.stop();consumer.stop()


def test_consumer_callback_and_automation_reconciliation_share_one_outbox(postgres_state,tmp_path,monkeypatch):
    from test_automation_recovery import NOW,STAMP,complete_report
    from app.workers.report_worker import ReportWorker
    cfg=postgres_state[1]
    consumer=WorkerProcess(cfg);automation=AutomationProcess(cfg)
    try:
        consumer.owner=consumer.repository.acquire('worker',ttl=30)
        automation.owner=automation.repository.acquire('automation',ttl=30)
        user=consumer.runtime.users.create_user(username='plan-admin',display_name='admin',password='test-password',role='admin',now=STAMP)
        auto=automation.runtime.automation
        plan=auto.create_schedule(dict(device_id='Cutter01',name='日报',report_type='leadership',frequency='daily',run_time='09:00',weekday=None,month_day=None,
            report_period='previous_day',recipients=['test@example.test'],subject_template='{report_name}',chart_type='bar_line',attach_report=False,
            max_attempts=2,enabled=True,next_run_at=STAMP),user=user,now=STAMP)
        automation.automation_runtime.apply(automation_enabled=True)
        with automation.scope():automation.automation_service.process_due_schedules(NOW)
        with auto.connect() as connection:
            runs=connection.execute('SELECT run_id FROM scheduled_report_runs').fetchall()
        assert len(runs)==1
        run=auto.get_run(runs[0]['run_id']);assert run['report_id']
        job=consumer.runtime.metadata.get_job(run['report_id'])
        assert not hasattr(ReportWorker(cfg,consumer.runtime.metadata),'automation_thread')
        complete_report(consumer.runtime.metadata,job['report_id'])
        # Completion callbacks and independent recovery race via PostgreSQL, never memory.
        def callback():
            with consumer.scope():consumer.runtime.automation_service.report_completed(job['report_id'],NOW)
        def reconcile():
            with automation.scope():automation.automation_service.reconcile_runs(NOW)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda f:f(),[callback,reconcile]))
        sends=[]
        monkeypatch.setattr(automation.runtime.email_service,'send',lambda *a,**kw:sends.append(1) or 'message-id')
        automation.automation_runtime.apply(smtp_enabled=True)
        with automation.scope():
            assert automation.automation_service.process_next_email(NOW)
            assert not automation.automation_service.process_next_email(NOW)
        assert sends==[1] and auto.get_run(run['run_id'])['status']=='sent'
        with auto.connect() as connection:assert connection.execute('SELECT count(*) FROM email_deliveries').fetchone()['count']==1
    finally:consumer.stop();automation.stop()


def test_role_diagnostics_include_all_snapshots_and_public_health_never_leaks_fingerprints(postgres_state):
    import json
    from app.models.configuration_contracts import ConfigurationDiagnostics
    database,cfg,_=postgres_state
    runtime=build_runtime(cfg,role='web')
    try:
        queue=CoordinationRepository(database)
        for role in CLASSES:
            owner=queue.acquire(role,ttl=30)
            queue.renew(role,owner,ttl=30,status={'report_alive':True,'scheduler_alive':True,'automation_alive':True,'email_alive':True,
                'configuration':runtime.configuration_diagnostics.observed(owner)})
        status=WorkerStatusReader(database).status()
        assert background_ready(status)
        report=runtime.configuration_diagnostics.report(status)
        ConfigurationDiagnostics.model_validate(report)
        assert all(report['processes'][role]['available'] for role in CLASSES)
        assert all(role in report['items'][0]['processes'] for role in CLASSES)
        assert 'digest' not in json.dumps(report) and cfg.postgres_password not in json.dumps(report)
        assert 'configuration' not in json.dumps(public_status(status))
        with database.connect() as connection:
            connection.execute("UPDATE process_leases SET expires_at=0 WHERE name='scheduler'")
        lost=WorkerStatusReader(database).status()
        assert not background_ready(lost) and lost['background_state']=='degraded'
        assert lost['roles']['worker']['state']=='running'
        assert not runtime.configuration_diagnostics.report(lost)['processes']['scheduler']['available']
    finally:runtime.database_runtime.close();runtime.database.close()


def test_consumer_owns_energy_recovery_and_hot_rebuild_keeps_inflight_claim(postgres_state,tmp_path,monkeypatch):
    from app.services.database_runtime import database_context
    cfg=state_settings(configuration(tmp_path,real=True,energy_api_enabled=True),postgres_state)
    consumer=WorkerProcess(cfg);scheduler=SchedulerProcess(cfg)
    consumer.owner=consumer.repository.acquire('worker',ttl=30)
    try:
        with consumer.scope():consumer._initialize_lines()
        old=database_context(consumer.runtime).lines['Cutter01'].cutting().energy_history_worker
        with consumer.scope():
            claim=old._claim()
            assert claim
            consumer.runtime.database_runtime.save_lines(database_context(consumer.runtime).catalog)
            # Force a real services rebuild without changing the history source identity.
            from app.services.runtime_settings_store import write_runtime_env
            write_runtime_env(cfg.runtime_settings_env_path,{'REPORT_BACKGROUND_REFRESH_SECONDS':60})
            consumer._initialize_lines()
        new=database_context(consumer.runtime).lines['Cutter01'].cutting().energy_history_worker
        assert new is not old and new.source_key==old.source_key
        with consumer.repository.connect() as connection:
            row=connection.execute('SELECT status,claim_token FROM energy_history_queue WHERE source_key=%s AND report_month=%s',(claim['source_key'],claim['report_month'])).fetchone()
            assert row=={'status':'running','claim_token':claim['claim_token']}
        # A deferred finish is handled by the consumer, never the scheduler copy.
        new._pending_finish=(claim,new._now(),new._now(),None)
        with consumer.scope():consumer._initialize_lines()
        assert new._pending_finish is None
        snapshot=database_context(scheduler.runtime).lines['Cutter01'].cutting().energy_history_worker
        monkeypatch.setattr(snapshot,'_flush_pending_finish',lambda:pytest.fail('scheduler flushed execution result'))
        scheduler.owner=scheduler.repository.acquire('scheduler',ttl=30)
        with scheduler.scope():scheduler._schedule()
    finally:consumer.stop();scheduler.stop()
