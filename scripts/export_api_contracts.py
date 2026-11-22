"""Export selected API contracts without starting services or opening databases."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from fastapi import FastAPI
from app.api import production_lines, devices, alarms, database, automation, configuration, analysis, reports
from app.modules.cutting import routes as cutting, workshop_routes as workshop, leadership_routes as leadership, equipment_routes as equipment, options_routes as options
from app.modules.energy import routes as energy
from app.modules.welding import routes as welding
from app.api.auth import status_router
from app.api.operations import router as operations_router
from app.api.database_monitors import router as database_monitors_router
from app.api.pdm_statistics import router as pdm_statistics_router


def api_schema() -> dict:
    app = FastAPI(title='Cutting report API contracts', version='1')
    app.include_router(status_router, prefix='/api/v1/auth')
    app.include_router(database_monitors_router)
    app.include_router(operations_router)
    app.include_router(pdm_statistics_router)
    for router in (production_lines.router, devices.router, workshop.router, cutting.router, cutting.callback_router, alarms.router, database.router, automation.router, configuration.router, welding.router, leadership.router, equipment.router, analysis.router, energy.router, reports.router, options.router):
        app.include_router(router)
    return app.openapi()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python scripts/export_api_contracts.py OUTPUT.json')
    target = Path(sys.argv[1])
    target.write_text(json.dumps(api_schema(), ensure_ascii=False, indent=2) + '\n')
