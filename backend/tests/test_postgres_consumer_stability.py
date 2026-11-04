"""Isolated PG18 observations; timed external substitutes are not production load."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import multiprocessing as mp
from pathlib import Path
from queue import Empty
import shutil
from threading import Event, Lock
from time import monotonic, sleep, time

import pytest
from psycopg_pool import PoolTimeout

from app.core.sql_capacity import sql_identity
from app.database.coordination import CoordinationRepository
from app.database.metadata import MetadataRepository
from app.database.postgres import PostgresDatabase, ExecutionLeaseLost, execution_scope
from app.services.llm_service import llm_capacity
from app.worker import WorkerProcess
from tests.line_fixtures import configuration
from tests.postgres_fixtures import state_settings
from tests.test_consumer_scaling import create_report
from tests.test_consumer_scaling_load import _stop


def _save_metrics(path, item):
    path = Path(path)
    records = json.loads(path.read_text()) if path.exists() else []
    records.append(item)
    path.write_text(json.dumps(records, indent=2) + '\n')


def _stress_consumer(cfg, stopped, ready, go, release, events, results):
    worker = WorkerProcess(cfg)
    pool = worker.runtime.database.pool
    metrics = {'instance': worker.instance_id, 'pool_limit': cfg.postgres_pool_max_size,
               'peak_checked_out': 0, 'pool_timeouts': 0, 'getconn_max_seconds': 0,
               'heartbeat_max_seconds': 0, 'heartbeats': 0, 'loop_errors': 0}
    lock, in_use, backend_pids = Lock(), set(), set()
    getconn, putconn = pool.getconn, pool.putconn
    def get(timeout=None):
        started = monotonic()
        try:
            conn = getconn(timeout)
        except PoolTimeout:
            with lock:
                metrics['pool_timeouts'] += 1
            raise
        with lock:
            in_use.add(id(conn))
            backend_pids.add(conn.info.backend_pid)
            metrics['peak_checked_out'] = max(metrics['peak_checked_out'], len(in_use))
            metrics['getconn_max_seconds'] = max(metrics['getconn_max_seconds'], monotonic()-started)
        return conn
    def put(conn):
        with lock:
            in_use.remove(id(conn))
        putconn(conn)
    pool.getconn, pool.putconn = get, put
    heartbeat = worker._heartbeat
    def renew():
        started = monotonic()
        heartbeat()
        with lock:
            metrics['heartbeat_max_seconds'] = max(metrics['heartbeat_max_seconds'], monotonic()-started)
            metrics['heartbeats'] += 1
    worker._heartbeat = renew
    def phase(task_id, line, delay=0):
        events.put(('task', 'start', time(), task_id, worker.instance_id, line))
        deadline = monotonic() + 60
        while not release.exists():
            assert monotonic() < deadline, 'external substitute release timed out'
            sleep(.03)
        sleep(delay)  # Pace arrivals below the existing bounded SQL waiting queue.
        context = worker.runtime.database_runtime.snapshot()
        source = context.database_sources.get(context.lines[line].config.source_key)
        with sql_identity(line), context.database_sources.capacity.slot(source):
            events.put(('sql', 'start', time(), task_id, worker.instance_id, line, source.id))
            sleep(.025)
            events.put(('sql', 'end', time(), task_id, worker.instance_id, line, source.id))
        with llm_capacity(cfg, worker.runtime.database):
            events.put(('model', 'start', time(), task_id, worker.instance_id, line))
            sleep(.015)
            events.put(('model', 'end', time(), task_id, worker.instance_id, line))
        with worker.runtime.pdf_service._render_slot():
            events.put(('pdf', 'start', time(), task_id, worker.instance_id, line))
            sleep(.01)
            events.put(('pdf', 'end', time(), task_id, worker.instance_id, line))
    def handle(task):
        with worker.runtime.database.connect() as conn:
            conn.execute('UPDATE background_tasks SET last_error=%s WHERE task_id=%s', ('TEST_STAGE', task['task_id']))
        phase(task['task_id'], task['device_id'], task['payload'].get('arrival_delay', 0))
        with worker.runtime.database.connect() as conn:
            conn.execute('UPDATE background_tasks SET last_error=NULL WHERE task_id=%s', (task['task_id'],))
        events.put(('task', 'end', time(), task['task_id'], worker.instance_id, task['device_id']))
    worker._handle = handle
    for line in worker.runtime.database_runtime.snapshot().lines.values():
        if line.pipeline is not None:
            original = line.pipeline.run
            def run(job, cancellation_check=None, original=original):
                phase(job['report_id'], job['device_id'])
                original(job, cancellation_check=cancellation_check)
                events.put(('task', 'end', time(), job['report_id'], worker.instance_id, job['device_id']))
            line.pipeline.run = run
    try:
        worker.start()
        ready.put((worker.instance_id, worker.owner))
        assert go.wait(20)
        while not stopped.is_set():
            try:
                worker.tick()
            except Exception:
                metrics['loop_errors'] += 1
                raise
            stopped.wait(.1)
        with worker.runtime.database.connect() as conn:
            metrics['idle_in_transaction'] = conn.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE pid=ANY(%s) AND state='idle in transaction' AND xact_start<clock_timestamp()-interval '1 second'", (list(backend_pids),)).fetchone()['n']
        metrics['backend_pids'] = sorted(backend_pids)
        metrics['checked_out_at_end'] = len(in_use)
        metrics['pool_stats'] = pool.get_stats()
    finally:
        worker.stop()
        results.put(metrics)


def _drain(channel):
    items = []
    while True:
        try:
            items.append(channel.get_nowait())
        except Empty:
            return items


@pytest.mark.parametrize('count,pool_size', [(1, 4), (1, 8), (1, 12), (3, 4)])
def test_real_workers_full_concurrency_pool_and_heartbeat(postgres_state, tmp_path, count, pool_size):
    database, _, _ = postgres_state
    cfg = state_settings(configuration(tmp_path, consumer_operation_concurrency=4, consumer_analysis_concurrency=10,
        sql_global_max_concurrent=2, sqlserver_default_max_concurrent=1, sql_line_max_concurrent=1,
        llm_max_concurrent=2, postgres_connect_timeout_seconds=5), postgres_state)
    cfg = replace(cfg, postgres_pool_max_size=pool_size, postgres_connect_timeout_seconds=5)
    cfg.echarts_vendor_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile('vendor/echarts.min.js', cfg.echarts_vendor_path)
    context = mp.get_context('spawn')
    ready, events, results = context.Queue(), context.Queue(), context.Queue()
    go, release = context.Event(), tmp_path / 'release-external'
    queue, metadata = CoordinationRepository(database), MetadataRepository(database)
    workers, owners, observed = [], {}, []
    peak_connections = peak_active_pg = lock_wait_samples = false_expiry = 0
    def sample():
        nonlocal peak_connections, peak_active_pg, lock_wait_samples, false_expiry
        with database.connect() as conn:
            rows = conn.execute("SELECT pid,state,wait_event_type FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND usename='cutting_app'").fetchall()
            peak_connections = max(peak_connections, len(rows))
            peak_active_pg = max(peak_active_pg, sum(r['state'] == 'active' for r in rows))
            lock_wait_samples += sum(r['wait_event_type'] == 'Lock' for r in rows)
            false_expiry += conn.execute("SELECT count(*) AS n FROM process_leases WHERE name LIKE 'worker:stress-%%' AND expires_at<=EXTRACT(EPOCH FROM clock_timestamp())").fetchone()['n']
    try:
        for i in range(count):
            stopped = context.Event()
            process = context.Process(target=_stress_consumer, args=(replace(cfg, consumer_instance_id=f'stress-{i}'), stopped, ready, go, release, events, results))
            process.start()
            instance, owner = ready.get(timeout=20)
            owners[instance] = owner
            workers.append((process, stopped, owner))
        assert len(set(owners.values())) == count
        for i in range(4*count):
            queue.enqueue(f'stress_operation_{i}', ('Cutter01','Cutter02')[i%2], {'arrival_delay':i*.03}, key=f'op-{i}')
        for i in range(10*count):
            queue.enqueue('analysis_stress', ('Cutter01','Cutter02')[i%2], {'arrival_delay':(i+4*count)*.03}, key=f'ai-{i}')
        for i in range(count):
            create_report(metadata, f'stress-report-{i}', report_type='equipment')
        started = monotonic()
        go.set()
        deadline = monotonic()+25
        while sum(e[:2] == ('task','start') for e in observed) < 15*count:
            assert monotonic() < deadline
            observed += _drain(events)
            sample()
            assert all(p.is_alive() for p,_,_ in workers)
            sleep(.03)
        # All 4+10+1 slots stay in external work across a real five-second heartbeat.
        held_until = monotonic()+6
        while monotonic() < held_until:
            sample()
            sleep(.05)
        release.touch()
        deadline = monotonic()+30
        while queue.task_counts().get('completed',0) != 14*count or any(metadata.get_job(f'stress-report-{i}')['status'] != 'completed' for i in range(count)):
            assert monotonic() < deadline
            observed += _drain(events)
            sample()
            assert all(p.is_alive() for p,_,_ in workers)
            sleep(.03)
        elapsed = monotonic()-started
        for _, stopped, _ in workers:
            stopped.set()
        for process,_,_ in workers:
            process.join(10)
            assert process.exitcode == 0
        observed += _drain(events)
        measured = [results.get(timeout=3) for _ in workers]
        starts = [e for e in observed if e[:2] == ('task','start')]
        assert len(starts) == len({e[3] for e in starts}) == 15*count
        peaks, active = {}, {}
        for kind, action, _, task, instance, line, *source in sorted(observed, key=lambda e:e[2]):
            keys = [kind]
            if kind == 'sql':
                keys += [f'line:{line}', f'source:{source[0]}']
            for key in keys:
                active[key] = active.get(key,0)+(1 if action=='start' else -1)
                peaks[key] = max(peaks.get(key,0),active[key])
        assert peaks['task'] == 15*count
        assert peaks['sql'] <= 2 and peaks['model'] <= 2 and peaks['pdf'] == 1
        assert all(n<=1 for k,n in peaks.items() if k.startswith(('source:','line:')))
        assert false_expiry == 0
        with database.connect() as conn:
            assert conn.execute("SELECT count(*) AS n FROM background_tasks WHERE attempts<>1 OR status<>'completed'").fetchone()['n'] == 0
            child_pids = [pid for item in measured for pid in item['backend_pids']]
            assert conn.execute('SELECT count(*) AS n FROM pg_stat_activity WHERE pid=ANY(%s)', (child_pids,)).fetchone()['n'] == 0
        for item in measured:
            assert item['pool_timeouts'] == item['loop_errors'] == item['checked_out_at_end'] == item['idle_in_transaction'] == 0, item
            assert item['heartbeats'] >= 2 and item['heartbeat_max_seconds'] < 5
            assert item['pool_stats']['requests_waiting'] == 0
            assert item['pool_stats']['pool_size'] == item['pool_stats']['pool_available']
        record = {'scenario':'normal','consumers':count,'pool_size':pool_size,'elapsed_seconds':elapsed,
                  'postgres_peak_connections_excluding_observer':peak_connections,'postgres_peak_active':peak_active_pg,
                  'lock_wait_samples':lock_wait_samples,'false_expiry':false_expiry,'false_recoveries':0,
                  'duplicate_claims':0,'resource_peaks':peaks,'instances':measured}
        _save_metrics('/tmp/stability-consumer-metrics.json', record)
        print(json.dumps(record))
    finally:
        release.touch();go.set()
        _stop(workers)
        for channel in (ready,events,results):
            channel.close()


@pytest.mark.parametrize('expires', [False, True])
def test_observed_lease_row_wait_and_commit_fence(postgres_state, expires):
    observer, cfg, _ = postgres_state
    with PostgresDatabase(replace(cfg, postgres_pool_max_size=4, postgres_connect_timeout_seconds=5)) as database:
        queue = CoordinationRepository(database)
        name = 'worker:row-lock'
        owner = queue.acquire(name, ttl=1 if expires else 30)
        entered, release = Event(), Event()
        pids = {}
        def protected():
            try:
                with execution_scope(database,name,owner), database.connect() as conn:
                    pids['holder'] = conn.info.backend_pid
                    conn.execute("INSERT INTO llm_waiters VALUES ('must-fence',0)")
                    entered.set()
                    assert release.wait(8)
                return 'committed'
            except ExecutionLeaseLost:
                return 'fenced'
        def renew():
            start = monotonic()
            result = queue.renew(name,owner,ttl=30)
            return result,monotonic()-start
        with ThreadPoolExecutor(2) as pool:
            writer=pool.submit(protected)
            try:
                assert entered.wait(3)
                renewal=pool.submit(renew)
                deadline=monotonic()+3
                blocked=None
                while blocked is None:
                    with observer.connect() as conn:
                        blocked=conn.execute("SELECT pid,wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers FROM pg_stat_activity WHERE datname=current_database() AND query LIKE 'SELECT name FROM process_leases%%FOR UPDATE' AND wait_event_type='Lock'").fetchone()
                        locks=conn.execute('SELECT mode,granted FROM pg_locks WHERE pid=%s', (pids['holder'],)).fetchall()
                    assert monotonic()<deadline
                    if blocked is None:sleep(.01)
                assert pids['holder'] in blocked['blockers']
                assert any(row['mode']=='RowShareLock' and row['granted'] for row in locks)
                sleep(1.2)
                release.set()
                written=writer.result(3)
                renewed,elapsed=renewal.result(3)
                assert written == ('fenced' if expires else 'committed')
                assert renewed is (not expires)
                with observer.connect() as conn:
                    assert conn.execute("SELECT count(*) AS n FROM llm_waiters WHERE waiter_id='must-fence'").fetchone()['n']==(0 if expires else 1)
                replacement=queue.acquire(name,ttl=30) if expires else None
                if expires:
                    assert replacement and replacement != owner
                _save_metrics('/tmp/stability-consumer-metrics.json', {'scenario':'expired_row_lock' if expires else 'valid_row_lock',
                    'heartbeat_wait_seconds':elapsed,'blocking_pids':blocked['blockers'],'wait_event':blocked['wait_event'],
                    'old_transaction':written,'renewed':renewed,'pool_stats':database.pool.get_stats(),'invalid_commits':0})
            finally:
                release.set()


def test_pool_exhaustion_times_out_then_returns_all_connections_and_fences_owner(postgres_state):
    observer,cfg,_=postgres_state
    with PostgresDatabase(replace(cfg, postgres_pool_max_size=4, postgres_connect_timeout_seconds=1)) as database:
        queue=CoordinationRepository(database)
        name='worker:pool-exhaustion'
        owner=queue.acquire(name,ttl=1.5)
        queue.enqueue('analysis_stress','Cutter01',{},key='pool-fault')
        task=queue.claim_task(owner,analysis=True)
        connections=[database.pool.getconn() for _ in range(4)]
        with observer.connect() as conn:
            before=conn.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'").fetchone()['n']
        def attempt(kind):
            started=monotonic()
            with pytest.raises(PoolTimeout):
                if kind=='claim':queue.claim_task(owner,analysis=True)
                elif kind=='renew':queue.renew(name,owner,ttl=30)
                else:queue.finish_task(task,owner)
            return monotonic()-started
        try:
            with ThreadPoolExecutor(3) as pool:
                durations=list(pool.map(attempt,('claim','renew','finish')))
        finally:
            for conn in connections:database.pool.putconn(conn)
        assert before==0 and min(durations)>=.9
        sleep(.6)
        other=CoordinationRepository(observer)
        replacement=other.acquire('worker:replacement',ttl=30)
        with execution_scope(observer,'worker:replacement',replacement):
            other.recover_tasks(replacement)
            other.admit_deferred()
            recovered=other.claim_task(replacement,analysis=True)
            assert recovered['task_id']==task['task_id'] and recovered['claim_token']!=task['claim_token']
            assert other.finish_task(recovered,replacement)
        with pytest.raises(ExecutionLeaseLost):queue.finish_task(task,owner)
        with pytest.raises(ExecutionLeaseLost),execution_scope(database,name,owner):database.healthy()
        assert database.healthy()
        stats=database.pool.get_stats()
        assert stats['requests_errors']==3 and stats['requests_waiting']==0
        assert stats['pool_available']==stats['pool_size']==4
        assert queue.task_counts()=={'completed':1}
        _save_metrics('/tmp/stability-consumer-metrics.json',{'scenario':'deliberate_pool_exhaustion','expected_pool_timeouts':3,
            'timeout_seconds':durations,'row_lock_waits':before,'pool_stats_after_recovery':stats,'invalid_commits':0})


def test_three_live_consumers_survive_one_kill_and_natural_lease_expiry(postgres_state, tmp_path):
    database, _, _ = postgres_state
    cfg = replace(state_settings(configuration(tmp_path, consumer_operation_concurrency=1,
        consumer_analysis_concurrency=1), postgres_state), postgres_pool_max_size=4, postgres_connect_timeout_seconds=5)
    context = mp.get_context('spawn')
    ready, events, results = context.Queue(), context.Queue(), context.Queue()
    go, release = context.Event(), tmp_path / 'release-external'
    go.set()
    queue = CoordinationRepository(database)
    workers, observed = [], []
    def start(index):
        stopped = context.Event()
        process = context.Process(target=_stress_consumer, args=(replace(cfg, consumer_instance_id=f'crash-{index}'), stopped, ready, go, release, events, results))
        process.start()
        identity, owner = ready.get(timeout=15)
        workers.append((process, stopped, owner))
        assert identity == f'crash-{index}'
        return owner
    try:
        first = start(0)
        queue.enqueue('stress_operation', 'Cutter01', {}, key='crash-dead')
        original = events.get(timeout=8)
        assert original[:2] == ('task','start') and original[4] == 'crash-0'
        second = start(1)
        queue.enqueue('stress_operation', 'Cutter02', {}, key='crash-live')
        live = events.get(timeout=8)
        assert live[:2] == ('task','start') and live[4] == 'crash-1'
        start(2)
        assert all(item['state']=='running' for item in queue.consumer_statuses())
        workers[0][0].kill();workers[0][0].join(5)
        started=monotonic()
        deadline=started+38
        recovered=None
        samples=0
        while recovered is None:
            observed += _drain(events)
            recovered=next((e for e in observed if e[:2]==('task','start') and e[3]==original[3]),None)
            with database.connect() as conn:
                survivor=conn.execute("SELECT status,owner,attempts FROM background_tasks WHERE dedup_key='crash-live'").fetchone()
                assert survivor=={'status':'running','owner':second,'attempts':1}
                for i in (1,2):
                    assert queue.worker_status(f'worker:crash-{i}')['state']=='running'
            samples+=1
            assert monotonic()<deadline and all(p.is_alive() for p,_,_ in workers[1:])
            sleep(.1)
        assert recovered[4]=='crash-2'
        with pytest.raises(ExecutionLeaseLost),execution_scope(database,'worker:crash-0',first):
            database.healthy()
        release.touch()
        deadline=monotonic()+10
        while queue.task_counts().get('completed',0)!=2:
            assert monotonic()<deadline
            sleep(.05)
        with database.connect() as conn:
            rows=conn.execute('SELECT dedup_key,status,attempts FROM background_tasks ORDER BY dedup_key').fetchall()
        assert rows==[{'dedup_key':'crash-dead','status':'completed','attempts':2},
                      {'dedup_key':'crash-live','status':'completed','attempts':1}]
        _save_metrics('/tmp/stability-consumer-metrics.json',{'scenario':'three_process_natural_crash_recovery',
            'recovery_seconds':monotonic()-started,'survivor_checks':samples,'false_recoveries':0,'invalid_commits':0,
            'final_tasks':rows})
    finally:
        release.touch()
        _stop(workers)
        for channel in (ready,events,results):channel.close()
