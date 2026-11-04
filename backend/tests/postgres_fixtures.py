"""Explicit PostgreSQL integration fixture; creates/drops only randomly named test DBs."""
from dataclasses import replace
import os
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
import pytest
from app.database.postgres import PostgresDatabase
from app.database.postgres_migrations import apply_migrations
from tests.line_fixtures import configuration

PASSWORD = os.environ.get('P5_TEST_POSTGRES_PASSWORD', 'p5-isolated-test-only')
PORT = int(os.environ.get('P5_TEST_POSTGRES_PORT', '55435'))


def _isolated_state(tmp_path, monkeypatch):
    if os.environ.get("P5_TEST_POSTGRES") != "1":
        pytest.skip("requires explicitly enabled isolated PostgreSQL")
    name = 'cutting_p5_' + uuid4().hex
    admin = dict(host='127.0.0.1', port=PORT, user='postgres', password=PASSWORD, dbname='p5_test')
    with psycopg.connect(**admin, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE DATABASE {} OWNER cutting_migrator').format(sql.Identifier(name)))
    cfg = configuration(tmp_path, app_env='test', postgres_host='127.0.0.1', postgres_port=PORT,
        postgres_database=name, postgres_user='cutting_app', postgres_password='p5-app-test-only',
        postgres_pool_max_size=8, postgres_connect_timeout_seconds=2)
    migrator = replace(cfg, postgres_user='cutting_migrator', postgres_password='p5-migrator-test-only')
    pools = []
    original_enter = PostgresDatabase.__enter__
    def entered(database):
        pools.append(database)
        return original_enter(database)
    monkeypatch.setattr(PostgresDatabase, '__enter__', entered)
    try:
        with PostgresDatabase(migrator) as database:
            apply_migrations(database)
            with database.connect() as connection:
                connection.execute('GRANT USAGE ON SCHEMA app_state TO cutting_app')
                connection.execute(Path('scripts/postgres/grant_runtime.sql').read_text().replace('BEGIN;', '').replace('COMMIT;', ''), prepare=False)
        with PostgresDatabase(cfg) as database:
            database.check_runtime()
            yield database, cfg, migrator
    finally:
        for database in reversed(pools):
            database.close()
        with psycopg.connect(**admin, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


@pytest.fixture
def postgres_state(tmp_path, monkeypatch):
    yield from _isolated_state(tmp_path, monkeypatch)


@pytest.fixture
def baseline_state(tmp_path, monkeypatch):
    yield from _isolated_state(tmp_path / 'baseline', monkeypatch)


@pytest.fixture
def state_database(postgres_state):
    from app.database.llm_usage import LlmUsageRepository
    LlmUsageRepository(postgres_state[0]).initialize()
    return postgres_state[0]


def state_settings(configuration, state):
    """Bind an existing business fixture to its isolated state DB, preserving sources."""
    from dataclasses import fields
    return replace(configuration, **{item.name:getattr(state[1],item.name)
                                    for item in fields(type(configuration)) if item.name.startswith('postgres_')})
