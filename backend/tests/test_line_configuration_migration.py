import importlib.util
import json
import os
from pathlib import Path

import pytest

from app.database.source_registry import DatabaseSourceRegistry
from app.device_config import load_line_catalog
from app.services.runtime_settings_store import read_runtime_env

PATH = Path(__file__).resolve().parents[2] / 'scripts/migrate_production_lines.py'
spec = importlib.util.spec_from_file_location('line_migration', PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
LEGACY = Path(__file__).resolve().parents[1] / 'app/configuration/devices.json'


def legacy():
    return json.loads(LEGACY.read_text())


def test_configuration_conversion_refuses_retired_sqlite_without_reading_it(tmp_path, monkeypatch):
    import sqlite3

    data = tmp_path / 'data'
    data.mkdir()
    database = data / 'metadata.db'
    database.write_bytes(b'preserve historical database')
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: pytest.fail('must not open SQLite'))
    with pytest.raises(ValueError, match='不再支持SQLite'):
        migration.migrate(tmp_path)
    assert database.read_bytes() == b'preserve historical database'
    assert not (data / 'settings.env').exists()


def test_migration_keeps_ids_business_bindings_credentials_and_existing_sources():
    extra = dict(id='quality', name='质量', engine='sqlserver', host='extra.test', port=1433,
                 database='extra', username='reader', password='existing-private')
    env = {'APP_DATA_SOURCE_MODE': 'sqlserver', 'SQLSERVER_HOST': 'cut.test', 'SQLSERVER_DATABASE': 'production',
           'SQLSERVER_USERNAME': 'reader', 'SQLSERVER_PASSWORD': 'literal-${PASSWORD}-\'secret',
           'WELD_ENABLED': 'true', 'WELD_DB_HOST': 'weld.test', 'WELD_DB_USER': 'reader', 'WELD_DB_PASSWORD': 'weld-private',
           'DATABASE_SOURCES_JSON': json.dumps([extra])}
    result = migration.migrate_values(env, legacy())
    catalog = load_line_catalog(result['PRODUCTION_LINES_JSON'])
    assert [line.id for line in catalog.lines] == ['Cutter01', 'Cutter02', 'welding']
    assert catalog.default_line_id == 'Cutter01'
    assert catalog.get('Cutter01').energy.model_dump(mode='json') == legacy()['devices'][0]['energy']
    sources = DatabaseSourceRegistry.from_json(result['DATABASE_SOURCES_JSON'])
    assert sources.get('quality').password.get_secret_value() == 'existing-private'
    assert sources.get('cutting-production').password.get_secret_value() == env['SQLSERVER_PASSWORD']
    assert result['WORKER_SOURCE_KEY'] == result['FILE_SOURCE_KEY'] == result['DISPATCH_SOURCE_KEY'] == 'cutting-production'
    assert migration.migrate_values({**env, **result}, legacy()) == {}


def test_repeatable_file_upgrade_backs_up_before_atomic_write_and_preserves_literal_password(tmp_path, monkeypatch):
    # Do not inherit the developer machine's configuration in a migration test.
    monkeypatch.setattr(migration.os, 'environ', {})
    directory = tmp_path / 'backend/app/configuration'
    directory.mkdir(parents=True)
    (directory / 'devices.json').write_text(LEGACY.read_text())
    env = tmp_path / '.env'
    env.write_text("APP_DATA_SOURCE_MODE=sqlserver\nSQLSERVER_HOST=cut.test\nSQLSERVER_DATABASE=production\nSQLSERVER_USERNAME=reader\nSQLSERVER_PASSWORD='literal-private'\n")
    assert migration.migrate(tmp_path)
    target = tmp_path / 'data/settings.env'
    before = target.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o600
    backup, = list((tmp_path / 'data').glob('p1-config-backup-*'))
    assert (backup / '.env').read_bytes() == env.read_bytes()
    assert (backup / 'devices.json').read_bytes() == LEGACY.read_bytes()
    assert all(file.stat().st_mode & 0o777 == 0o600 for file in backup.iterdir() if not file.name.endswith(".lock"))
    assert not migration.migrate(tmp_path)
    assert before == target.read_bytes()
    assert len(list((tmp_path / 'data').glob('p1-config-backup-*'))) == 1


def test_invalid_migration_does_not_publish_or_fallback_and_collision_does_not_overwrite():
    env = {'APP_DATA_SOURCE_MODE': 'sqlserver'}
    with pytest.raises(ValueError):
        migration.migrate_values(env, legacy())
    prior = dict(id='cutting-production', name='已有登记', engine='sqlserver', enabled=False,
                 host='', port=1433, database='', username='', password='keep-private')
    result = migration.migrate_values({'DATABASE_SOURCES_JSON': json.dumps([prior])}, legacy())
    sources = DatabaseSourceRegistry.from_json(result['DATABASE_SOURCES_JSON'])
    assert sources.get('cutting-production').password.get_secret_value() == 'keep-private'
    assert load_line_catalog(result['PRODUCTION_LINES_JSON']).get('Cutter01').source_key == 'cutting-production-2'
    with pytest.raises(ValueError):
        migration.migrate_values({'PRODUCTION_LINES_JSON': 'invalid'}, legacy())


def test_migration_normalizes_old_flags_and_preserves_optional_energy():
    value = legacy()
    value['devices'][1].pop('energy', None)
    migrated = migration.migrate_values({'APP_DATA_SOURCE_MODE': ' DEMO ', 'WELD_ENABLED': ' FALSE '}, value)
    catalog = load_line_catalog(migrated['PRODUCTION_LINES_JSON'])
    assert catalog.get('Cutter02').energy is None
    assert not catalog.get('welding').enabled
    with pytest.raises(ValueError, match='模式'):
        migration.migrate_values({'APP_DATA_SOURCE_MODE': 'unknown'}, value)
