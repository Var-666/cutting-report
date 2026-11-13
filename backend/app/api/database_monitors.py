"""Global monitoring targets, independent of the selected production line."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import require_admin
from app.api.runtime import application_state
from app.database.database_monitors import DuplicateMonitor, MonitorNotFound
from app.models.contracts import ApiEnvelope, Page
from app.models.database_monitor import (
    DatabaseMonitorList, DatabaseMonitorIncident, DatabaseMonitorInput, DatabaseMonitorSaved, DatabaseMonitorDeleted,
)

router = APIRouter(prefix='/api/v1/database-monitors', tags=['database-monitors'], dependencies=[Depends(require_admin)])


@router.get('', response_model=ApiEnvelope[DatabaseMonitorList])
def list_monitors(request: Request):
    service = application_state(request).database_monitor
    return {'success': True, 'data': {'items': service.list(), 'timezone': service.timezone}, 'request_id': request.state.request_id}


@router.post('', response_model=ApiEnvelope[DatabaseMonitorSaved])
def create_monitor(request: Request, item: DatabaseMonitorInput):
    return save(request, item)


@router.put('/{monitor_id}', response_model=ApiEnvelope[DatabaseMonitorSaved])
def update_monitor(request: Request, monitor_id: str, item: DatabaseMonitorInput):
    return save(request, item, monitor_id)


def save(request: Request, item: DatabaseMonitorInput, monitor_id: str | None = None):
    try:
        identifier = application_state(request).database_monitor.repository.save(item, monitor_id)
    except DuplicateMonitor as exc:
        raise HTTPException(409, str(exc)) from None
    except MonitorNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {'success': True, 'data': {'monitor_id': identifier}, 'request_id': request.state.request_id}


@router.delete('/{monitor_id}', response_model=ApiEnvelope[DatabaseMonitorDeleted])
def delete_monitor(request: Request, monitor_id: str):
    try:
        application_state(request).database_monitor.repository.delete(monitor_id)
    except MonitorNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {'success': True, 'data': {'deleted': True}, 'request_id': request.state.request_id}


@router.get('/incidents/page', response_model=ApiEnvelope[Page[DatabaseMonitorIncident]])
def incidents(request: Request, page: int = Query(1, ge=1), page_size: int = Query(15, ge=1, le=100)):
    return {'success': True, 'data': application_state(request).database_monitor.repository.incidents(page, page_size), 'request_id': request.state.request_id}
