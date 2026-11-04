"""Real Consumer processes and PostgreSQL; external work is a timed substitute."""
from dataclasses import replace
import json
import math
import multiprocessing as mp
from pathlib import Path
from queue import Empty
from statistics import mean
from time import monotonic, sleep, time

import pytest

from app.core.sql_capacity import sql_identity
from app.database.coordination import CoordinationRepository
from app.database.postgres import ExecutionLeaseLost, execution_scope
from app.services.llm_service import llm_capacity
from app.worker import WorkerProcess
from tests.line_fixtures import configuration
from tests.postgres_fixtures import state_settings
from tests.test_consumer_scaling import create_report


def _consumer(cfg, stopped, ready, go, events, hold=None):
    process = WorkerProcess(cfg)
    process.stop_event = stopped
    def handle(task):
        identity = (task['task_id'], process.instance_id, task['device_id'])
        events.put(('task', 'start', time(), *identity, task['created_at']))
        if hold is not None and process.instance_id in ('first', 'second'):
            deadline = monotonic() + 30
            while not hold.exists():
                assert monotonic() < deadline
                sleep(.01)
        context = process.runtime.database_runtime.snapshot()
        config = context.database_sources.get(context.lines[task['device_id']].config.source_key)
        with sql_identity(task['device_id']), context.database_sources.capacity.slot(config):
            events.put(('sql', 'start', time(), *identity, config.id))
            sleep(.1)
            events.put(('sql', 'end', time(), *identity, config.id))
        with llm_capacity(cfg, process.runtime.database):
            events.put(('model', 'start', time(), *identity))
            sleep(.05)
            events.put(('model', 'end', time(), *identity))
        with process.runtime.pdf_service._render_slot():
            events.put(('pdf', 'start', time(), *identity))
            sleep(.05)
            events.put(('pdf', 'end', time(), *identity))
        events.put(('task', 'end', time(), *identity))
    process._handle = handle
    try:
        process.start()
        ready.put((process.instance_id, process.owner))
        assert go.wait(20)
        while not stopped.is_set():
            process.tick()
            stopped.wait(1)  # Same polling cadence as the production run loop.
    finally:
        process.stop()


def _start(context, cfg, ready, go, events, hold=None):
    stopped = context.Event()
    process = context.Process(target=_consumer, args=(cfg, stopped, ready, go, events, hold))
    process.start()
    instance, owner = ready.get(timeout=15)
    assert instance == cfg.consumer_instance_id
    return process, stopped, owner


def _stop(items):
    for process, stopped, owner in items:
        if process.is_alive():
            stopped.set()
    for process, stopped, owner in items:
        process.join(8)
        if process.is_alive():
            process.kill()
            process.join(5)


def _events(channel):
    items = []
    while True:
        try:
            items.append(channel.get_nowait())
        except Empty:
            return items


def test_one_two_three_real_consumers_record_waiting_parallelism_connections_and_shared_limits(postgres_state, tmp_path):
    database, _, _ = postgres_state
    cfg = state_settings(configuration(tmp_path, real=True, welding=True,
        consumer_operation_concurrency=1, consumer_analysis_concurrency=1,
        sql_global_max_concurrent=2, sqlserver_default_max_concurrent=1, mysql_default_max_concurrent=1,
        sql_line_max_concurrent=1, llm_max_concurrent=2, postgres_pool_max_size=4), postgres_state)
    cfg = replace(cfg, postgres_pool_max_size=4)
    context = mp.get_context('spawn')
    queue = CoordinationRepository(database)
    metrics = []
    for count in (1, 2, 3):
        ready, events, go = context.Queue(), context.Queue(), context.Event()
        processes = []
        try:
            for index in range(count):
                processes.append(_start(context, replace(cfg, consumer_instance_id=f'load-{count}-{index}'), ready, go, events))
            for index in range(9):
                line = ('Cutter01', 'Cutter02', 'welding')[index % 3]
                queue.enqueue('current_refresh', line, {'source_fingerprint':line}, key=f'load-{count}-{index}')
            started = monotonic()
            go.set()
            deadline = monotonic() + 25
            connections = 0
            while queue.task_counts().get('completed', 0) < (len(metrics) + 1) * 9:
                assert monotonic() < deadline
                assert all(item[0].is_alive() for item in processes)
                with database.connect() as connection:
                    value = connection.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND usename='cutting_app'").fetchone()['n']
                    connections = max(connections, value)
                sleep(.1)
            elapsed = monotonic() - started
            observed = _events(events)
            starts = [item for item in observed if item[:2] == ('task', 'start')]
            assert len(starts) == len({item[3] for item in starts}) == 9
            waiting = sorted(item[2] - item[6] for item in starts)
            active, peaks = {}, {}
            for event in sorted(observed, key=lambda item:item[2]):
                kind, action, stamp, task_id, instance, line, *extra = event
                keys = [kind]
                if kind == 'sql':
                    keys += [f'source:{extra[0]}', f'line:{line}']
                for key in keys:
                    active[key] = active.get(key, 0) + (1 if action == 'start' else -1)
                    peaks[key] = max(peaks.get(key, 0), active[key])
            assert peaks['sql'] <= 2 and peaks['model'] <= 1 and peaks['pdf'] <= 1
            assert all(value <= 1 for key,value in peaks.items() if key.startswith(('source:', 'line:')))
            assert peaks['task'] > 1 if count > 1 else peaks['task'] == 1
            assert connections <= cfg.postgres_pool_max_size * count + postgres_state[1].postgres_pool_max_size
            metrics.append({'consumers':count, 'completed':9, 'elapsed_seconds':round(elapsed,3),
                'mean_wait_seconds':round(mean(waiting),3), 'p95_wait_seconds':round(waiting[math.ceil(.95*len(waiting))-1],3),
                'parallel_tasks':peaks['task'], 'postgres_connections':connections, 'sql_peak':peaks['sql'],
                'source_peaks':{key:value for key,value in peaks.items() if key.startswith('source:')},
                'model_peak':peaks['model'], 'pdf_peak':peaks['pdf'], 'duplicate_claims':0})
        finally:
            _stop(processes)
            ready.close();events.close()
    Path('/tmp/p7-consumer-metrics.json').write_text(json.dumps(metrics, indent=2)+'\n')
    print(json.dumps(metrics))


def test_live_consumers_recover_only_a_crashed_peer_without_restarting_scheduler_or_other_consumers(postgres_state, tmp_path):
    database, _, _ = postgres_state
    cfg = state_settings(configuration(tmp_path, real=True, welding=True, consumer_operation_concurrency=1), postgres_state)
    context = mp.get_context('spawn')
    ready, events, go = context.Queue(), context.Queue(), context.Event()
    hold = tmp_path / 'release-blocked-work'
    queue = CoordinationRepository(database)
    processes = []
    try:
        first = _start(context, replace(cfg, consumer_instance_id='first'), ready, go, events, hold)
        second = _start(context, replace(cfg, consumer_instance_id='second'), ready, go, events, hold)
        processes.extend((first, second))
        queue.enqueue('current_refresh', 'Cutter01', {'source_fingerprint':'one'}, key='crash-a')
        queue.enqueue('current_refresh', 'Cutter02', {'source_fingerprint':'two'}, key='crash-b')
        go.set()
        started = [events.get(timeout=10), events.get(timeout=10)]
        assert all(item[:2] == ('task', 'start') for item in started)
        first[0].kill();first[0].join(5)
        with database.connect() as connection:
            connection.execute("UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())-1 WHERE name='worker:first'")
        third = _start(context, replace(cfg, consumer_instance_id='third'), ready, go, events)
        processes.append(third)
        deadline = monotonic() + 12
        while queue.task_counts().get('completed', 0) != 1:
            assert monotonic() < deadline
            sleep(.05)
        with database.connect() as connection:
            live = connection.execute("SELECT * FROM background_tasks WHERE owner=%s AND status='running'", (second[2],)).fetchall()
            assert len(live) == 1
        with pytest.raises(ExecutionLeaseLost), execution_scope(database, 'worker:first', first[2]):
            database.healthy()
        hold.touch()
        deadline = monotonic() + 8
        while queue.task_counts().get('completed', 0) != 2:
            assert monotonic() < deadline
            sleep(.05)
        assert second[0].is_alive() and third[0].is_alive()
    finally:
        hold.touch()
        _stop(processes)
        ready.close();events.close()


def _report_consumer(cfg, stopped, ready, started, release):
    process = WorkerProcess(cfg)
    process.stop_event = stopped
    for line in process.runtime.database_runtime.snapshot().lines.values():
        if line.pipeline is None:
            continue
        original = line.pipeline.run
        def run(job, cancellation_check=None, original=original):
            started.put((process.instance_id, job['report_id'], process.owner))
            if process.instance_id != 'report-third':
                deadline = monotonic() + 30
                while not release.exists():
                    assert monotonic() < deadline
                    sleep(.01)
            original(job, cancellation_check=cancellation_check)
        line.pipeline.run = run
    try:
        process.start()
        ready.put(process.owner)
        while not stopped.wait(.1):
            process.tick()
    finally:
        process.stop()


def test_report_process_crash_recovers_same_report_id_and_publishes_only_new_owner_file(postgres_state, tmp_path):
    import shutil
    from app.database.metadata import MetadataRepository
    database, cfg, _ = postgres_state
    cfg = replace(cfg, worker_poll_seconds=.05)
    cfg.echarts_vendor_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile('vendor/echarts.min.js', cfg.echarts_vendor_path)
    context = mp.get_context('spawn')
    ready, started = context.Queue(), context.Queue()
    release = tmp_path / 'release-report'
    items = []
    metadata = MetadataRepository(database)
    def start(name):
        stopped = context.Event()
        process = context.Process(target=_report_consumer, args=(replace(cfg, consumer_instance_id=name),stopped,ready,started,release))
        process.start()
        owner = ready.get(timeout=15)
        item = process, stopped, owner
        items.append(item)
        return item
    try:
        first = start('report-first')
        create_report(metadata, 'crash-report-a', report_type='equipment')
        original = started.get(timeout=10)
        assert original[0] == 'report-first'
        second = start('report-second')
        create_report(metadata, 'crash-report-b', report_type='equipment')
        live = started.get(timeout=10)
        assert live[0] == 'report-second'
        first[0].kill();first[0].join(5)
        with database.connect() as connection:
            connection.execute("UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())-1 WHERE name='worker:report-first'")
        third = start('report-third')
        recovered = started.get(timeout=10)
        assert recovered[1] == original[1] and recovered[2] == third[2]
        deadline = monotonic() + 12
        while metadata.get_job(original[1])['status'] not in ('completed', 'failed'):
            assert monotonic() < deadline
            sleep(.05)
        job = metadata.get_job(original[1])
        assert job['status'] == 'completed', job
        assert third[2] in job['relative_path'] and first[2] not in job['relative_path']
        assert (cfg.reports_dir / job['relative_path']).is_file()
        assert metadata.get_job(live[1])['status'] == 'querying'
        assert metadata.get_job(live[1])['execution_owner'] == second[2]
        with execution_scope(database, 'worker:report-first', first[2]), pytest.raises(ExecutionLeaseLost):
            metadata.update_status(original[1], 'failed', 'stale')
        release.touch()
        deadline = monotonic() + 10
        while metadata.get_job(live[1])['status'] not in ('completed', 'failed'):
            assert monotonic() < deadline
            sleep(.05)
        assert metadata.get_job(live[1])['status'] == 'completed'
        with database.connect() as connection:
            assert connection.execute('SELECT count(*) AS n FROM reports').fetchone()['n'] == 2
    finally:
        release.touch()
        _stop(items)
        ready.close();started.close()


def test_scale_up_and_down_keeps_an_existing_running_task_owned(postgres_state, tmp_path):
    database, _, _ = postgres_state
    cfg = state_settings(configuration(tmp_path, real=True, welding=True, consumer_operation_concurrency=1), postgres_state)
    context = mp.get_context('spawn')
    ready, events, go = context.Queue(), context.Queue(), context.Event()
    release = tmp_path / 'release-scaling-task'
    queue = CoordinationRepository(database)
    items = []
    try:
        first = _start(context, replace(cfg, consumer_instance_id='first'), ready, go, events, release)
        items.append(first)
        queue.enqueue('current_refresh', 'Cutter01', {'source_fingerprint':'one'}, key='scaling-held')
        go.set()
        assert events.get(timeout=10)[:2] == ('task', 'start')
        second = _start(context, replace(cfg, consumer_instance_id='second'), ready, go, events)
        items.append(second)
        third = _start(context, replace(cfg, consumer_instance_id='third'), ready, go, events)
        items.append(third)
        for line in ('Cutter02', 'welding'):
            queue.enqueue('current_refresh', line, {'source_fingerprint':line}, key='scaling-'+line)
        deadline = monotonic() + 10
        while queue.task_counts().get('completed', 0) != 2:
            assert monotonic() < deadline
            sleep(.05)
        with database.connect() as connection:
            row = connection.execute("SELECT status,owner,attempts FROM background_tasks WHERE dedup_key='scaling-held'").fetchone()
            assert row == {'status':'running', 'owner':first[2], 'attempts':1}
        _stop([second, third])
        with database.connect() as connection:
            live = connection.execute("SELECT owner FROM process_leases WHERE name LIKE 'worker:%' AND expires_at>EXTRACT(EPOCH FROM clock_timestamp())").fetchall()
            assert live == [{'owner':first[2]}]
        release.touch()
        deadline = monotonic() + 8
        while queue.task_counts().get('completed', 0) != 3:
            assert monotonic() < deadline
            sleep(.05)
        assert first[0].is_alive()
    finally:
        release.touch()
        _stop(items)
        ready.close();events.close()
