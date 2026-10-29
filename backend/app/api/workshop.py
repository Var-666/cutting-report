from __future__ import annotations
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.workloads import query_requests
from app.api.devices import device_state, device_settings
from app.api.auth import require_admin, require_user, require_worker
from app.config import settings
from app.database.sqlserver import SqlServerConfigurationError
from app.database.workshop_errors import (
    ConfirmationConflictError, ConfirmationNotFoundError, ConfirmationPermissionError,
    DailyReportRecordMismatchError, DailyReportScopeMismatchError, DailyReportSourceMismatchError,
)
from app.models.contracts import ApiEnvelope, Items
from app.models.workshop_contracts import (
    WorkshopResult, WorkshopReportInfo, LatestProductionDate, TaskConfirmation,
    ConfirmationBatch, ConfirmationAudit,
)
from app.models.workshop_requests import (
    ConfirmTaskRequest, ConfirmSnapshotTaskRequest, ConfirmTasksRequest,
    RevokeTaskRequest, CorrectTaskRequest,
)
from app.services.workshop_service import WorkshopService, WorkshopTaskNotFoundError, _workshop_trend
from app.services.workshop_sorting import sort_workshop_by_recency

router = APIRouter(prefix='/api/v1/workshop', tags=['workshop-confirmation'], dependencies=[Depends(require_user), Depends(device_state)])


def _envelope(request: Request, data: object) -> dict:
    return {'success': True, 'data': data, 'request_id': request.state.request_id}


def _service(request: Request) -> WorkshopService:
    runtime = device_state(request)
    # Embedded callers can still supply the historical combined repository.
    reports = getattr(runtime, 'workshop', None)
    confirmations = runtime.confirmations if hasattr(runtime, 'confirmations') else reports
    return WorkshopService(reports, confirmations, device_settings(request),
                           query_service=getattr(runtime, 'query_service', None),
                           range_report_service=getattr(runtime, 'range_report_service', None))


def _invoke(operation, *, value_error_status=None, **kwargs):
    try:
        return operation(**kwargs)
    except (WorkshopTaskNotFoundError, ConfirmationNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfirmationPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ConfirmationConflictError, DailyReportRecordMismatchError,
            DailyReportScopeMismatchError, DailyReportSourceMismatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        if value_error_status is None:
            raise
        raise HTTPException(status_code=value_error_status, detail=str(exc)) from exc


def sort_workshop_records(records: list, sort_mode: Literal['manager', 'worker']):
    return sort_workshop_by_recency(records, settings.timezone)


@query_requests.route(router.get('/latest-date', response_model=ApiEnvelope[LatestProductionDate], response_model_exclude_unset=True))
def latest_production_date(request: Request, on_or_before: date, user: dict=Depends(require_user)) -> dict:
    return _envelope(request, _invoke(_service(request).latest_production_date, on_or_before=on_or_before, user=user))

@query_requests.route(router.post('/reports', response_model=ApiEnvelope[WorkshopReportInfo], response_model_exclude_unset=True))
def generate_daily_report(payload: ConfirmTaskRequest, request: Request, user: dict=Depends(require_user)) -> dict:
    return _envelope(request, _invoke(_service(request).generate_daily_report, payload=payload, user=user))

@query_requests.route(router.get('/tasks', response_model=ApiEnvelope[WorkshopResult], response_model_exclude_unset=True))
def list_tasks(request: Request, report_date: date, project_name: str | None=Query(default=None, max_length=128), confirmation_status: Literal['all', 'confirmed', 'unconfirmed']='all', sort_mode: Literal['manager', 'worker']='manager', page: int=Query(default=1, ge=1), page_size: int=Query(default=10, ge=10, le=100), warmup: bool=False, user: dict=Depends(require_user)) -> dict:
    return _envelope(request, _invoke(_service(request).list_tasks, report_date=report_date, project_name=project_name, confirmation_status=confirmation_status, sort_mode=sort_mode, page=page, page_size=page_size, warmup=warmup, user=user))

@query_requests.route(router.get('/range', response_model=ApiEnvelope[WorkshopResult], response_model_exclude_unset=True))
def list_range_tasks(request: Request, start_date: date, end_date: date, project_name: str | None=Query(default=None, max_length=128), confirmation_status: Literal['all', 'confirmed', 'unconfirmed']='all', page: int=Query(default=1, ge=1), page_size: int=Query(default=10, ge=10, le=100), _: dict=Depends(require_admin)) -> dict:
    try:
        return _envelope(request, _invoke(_service(request).list_range_tasks, value_error_status=422, start_date=start_date, end_date=end_date, project_name=project_name, confirmation_status=confirmation_status, page=page, page_size=page_size))
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@router.post('/tasks/{task_id}/confirm', response_model=ApiEnvelope[TaskConfirmation], response_model_exclude_unset=True)
def confirm_task(task_id: str, payload: ConfirmSnapshotTaskRequest, request: Request, user: dict=Depends(require_worker)) -> dict:
    return _envelope(request, _invoke(_service(request).confirm_task, task_id=task_id, payload=payload, user=user))

@router.post('/tasks/confirm', response_model=ApiEnvelope[ConfirmationBatch], response_model_exclude_unset=True)
def confirm_tasks(payload: ConfirmTasksRequest, request: Request, user: dict=Depends(require_worker)) -> dict:
    return _envelope(request, _invoke(_service(request).confirm_tasks, payload=payload, user=user))

@router.post('/tasks/{task_id}/revoke', response_model=ApiEnvelope[ConfirmationAudit], response_model_exclude_unset=True)
def revoke_task_confirmation(task_id: str, payload: RevokeTaskRequest, request: Request, user: dict=Depends(require_worker)) -> dict:
    return _envelope(request, _invoke(_service(request).revoke_task_confirmation, task_id=task_id, payload=payload, user=user))

@router.post('/tasks/{task_id}/correct', response_model=ApiEnvelope[ConfirmationAudit], response_model_exclude_unset=True)
def correct_task_confirmation(task_id: str, payload: CorrectTaskRequest, request: Request, admin: dict=Depends(require_admin)) -> dict:
    return _envelope(request, _invoke(_service(request).correct_task_confirmation, task_id=task_id, payload=payload, admin=admin, value_error_status=409))

@router.get('/confirmation-audits', response_model=ApiEnvelope[Items[ConfirmationAudit]], response_model_exclude_unset=True)
def list_confirmation_audits(request: Request, report_date: date | None=None, task_id: str | None=Query(default=None, max_length=128), limit: int=Query(default=30, ge=1, le=100), _: dict=Depends(require_admin)) -> dict:
    return _envelope(request, _invoke(_service(request).list_confirmation_audits, report_date=report_date, task_id=task_id, limit=limit))
