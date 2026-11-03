"""Explicit isolated line/source fixtures; no site credentials or database connections."""
from pathlib import Path
import json

from app.config import Settings, settings_for_line
from app.device_config import load_line_catalog
from app.database.source_registry import DatabaseSourceRegistry, source_configs_json
from app.models.database_sources import DatabaseSourceConfig

EXAMPLE = Path(__file__).resolve().parents[1] / 'app/configuration/production_lines.example.json'


def configuration(root: Path, *, real: bool = False, welding: bool = False, **overrides) -> Settings:
    value = json.loads(EXAMPLE.read_text())
    value['default_line_id'] = 'Cutter01'
    value['lines'][-1]['enabled'] = welding
    sources = [DatabaseSourceConfig(id='cutting-production', name='切割', engine='sqlserver', enabled=real,
        host='cutting.test', port=1433, database='production', username='reader', password='cutting-private'),
        DatabaseSourceConfig(id='welding-production', name='焊接', engine='mysql', enabled=welding,
        host='welding.test', port=3306, database='welding', username='reader', password='welding-private')]
    values = dict(project_root=root, production_lines_json=json.dumps(value), database_sources_json=source_configs_json(sources),
                  data_source_mode='sqlserver' if real else 'demo', worker_database_auth_enabled=False,
                  file_database_enabled=False, magic_box_enabled=False, energy_api_enabled=False, llm_enabled=False,
                  alarm_monitor_enabled=False, smtp_enabled=False)
    values.update(overrides)
    return Settings(**values)


def line_settings(cfg: Settings, line_id: str = 'Cutter01') -> Settings:
    catalog = load_line_catalog(cfg.production_lines_json)
    registry = DatabaseSourceRegistry.from_json(cfg.database_sources_json)
    line = catalog.get(line_id)
    return settings_for_line(cfg, line, registry.fingerprint(line.source_key))


def cutting_settings(**overrides) -> Settings:
    """A selected cutting operation, as distinct from the application's global settings."""
    root = overrides.pop('project_root', Path('/tmp/cutting-unit-test'))
    selected = overrides.pop('device_id', 'Cutter01')
    cfg = line_settings(configuration(root, real=overrides.get("data_source_mode") == "sqlserver"), selected)
    from dataclasses import replace
    return replace(cfg, **overrides)


def welding_settings(**overrides) -> Settings:
    root = overrides.pop('project_root', Path('/tmp/welding-unit-test'))
    cfg = line_settings(configuration(root, welding=True), 'welding')
    from dataclasses import replace
    return replace(cfg, **overrides)


def runtime_line(service, line_id='Cutter01'):
    from app.services.production_lines import ProductionLineRuntime
    cfg = configuration(Path('/tmp/line-unit-test'), welding=True)
    return ProductionLineRuntime(load_line_catalog(cfg.production_lines_json).get(line_id), service,
                                 background=service if line_id.startswith('Cutter') else None, pipeline=getattr(service, 'pipeline', None))


CUTTING_SCOPES = {line.id: line.machine_no for line in load_line_catalog(EXAMPLE.read_text()).lines if line.type == 'cutting'}


def as_line_services(services):
    return {key: runtime_line(value, key) for key, value in services.items()}


def source_client(cfg):
    config = DatabaseSourceRegistry.from_json(cfg.database_sources_json).get(cfg.production_data_source)
    return DatabaseSourceRegistry([config.model_copy(update={"enabled": True})]).client(config.id)
