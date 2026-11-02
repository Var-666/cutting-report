"""Export selected API contracts without starting services or opening databases."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from fastapi import FastAPI
from app.api import alarms, cutting, workshop, database, automation, configuration, welding, leadership, equipment, analysis, energy, reports, options


def api_schema() -> dict:
    app = FastAPI(title='Cutting report API contracts', version='1')
    for router in (workshop.router, cutting.router, alarms.router, database.router, automation.router, configuration.router, welding.router, leadership.router, equipment.router, analysis.router, energy.router, reports.router, options.router):
        app.include_router(router)
    return app.openapi()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python scripts/export_api_contracts.py OUTPUT.json')
    target = Path(sys.argv[1])
    target.write_text(json.dumps(api_schema(), ensure_ascii=False, indent=2) + '\n')
