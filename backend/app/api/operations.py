"""Global operations routes and authenticated, bounded browser observations."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from psycopg import Error as PostgresError
from psycopg_pool import PoolTimeout, TooManyRequests

from app.api.auth import require_admin, require_user
from app.api.runtime import application_state
from app.api.workloads import RequestWorkload
from app.models.contracts import ApiEnvelope, Page
from app.models.operations import (
    AcknowledgeIncident, BrowserIssue, Incident, IncidentFilters, LogFilters, LogPage,
    OperationsOverview, OperationsStatus, RelatedKind, RelatedOperation, ReportedIssue,
)

router = APIRouter(prefix='/api/v1/operations', tags=['operations'])
reads = RequestWorkload(2, '运维查询')
Identifier = Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]
Admin = Annotated[dict, Depends(require_admin)]


def envelope(request, data):
    return dict(success=True, data=data, request_id=request.state.request_id)


async def read(request, operation, *args):
    try:
        return envelope(request, await reads.run_sync(operation, *args, _request=request))
    except (PostgresError, PoolTimeout, TooManyRequests):
        raise HTTPException(503, '运维状态库暂不可用，请稍后重试') from None


@router.get('/overview', response_model=ApiEnvelope[OperationsOverview])
async def overview(request: Request, admin: Admin):
    return await read(request, application_state(request).operations.overview)


@router.get('/status', response_model=ApiEnvelope[OperationsStatus])
async def status(request: Request, admin: Admin):
    return await read(request, application_state(request).operations.status)


@router.get('/pending-notice', response_model=ApiEnvelope[bool])
async def pending_notice(request: Request, response: Response, admin: Admin):
    response.headers['Cache-Control'] = 'no-store'
    return await read(request, application_state(request).operations.repository.has_pending_incident)


@router.get('/incidents', response_model=ApiEnvelope[Page[Incident]])
async def incidents(request: Request, admin: Admin, filters: Annotated[IncidentFilters, Query()]):
    return await read(request, application_state(request).operations.repository.list, filters)


@router.get('/incidents/{incident_id}', response_model=ApiEnvelope[Incident])
async def incident(request: Request, admin: Admin, incident_id: Identifier):
    response = await read(request, application_state(request).operations.repository.get, incident_id)
    if response['data'] is None:
        raise HTTPException(404, '异常事件不存在')
    return response


@router.post('/incidents/{incident_id}/acknowledge', response_model=ApiEnvelope[Incident])
async def acknowledge(request: Request, admin: Admin, incident_id: Identifier, payload: AcknowledgeIncident):
    response = await read(request, application_state(request).operations.repository.acknowledge,
                          incident_id, admin['user_id'], payload.note)
    if response['data'] is None:
        raise HTTPException(409, '事件已确认、已恢复或不存在')
    return response


@router.get('/logs', response_model=ApiEnvelope[LogPage])
async def logs(request: Request, admin: Admin, filters: Annotated[LogFilters, Query()]):
    return await read(request, application_state(request).operations.logs.query, filters)


@router.post('/browser', response_model=ApiEnvelope[ReportedIssue])
def browser(request: Request, payload: BrowserIssue, user: Annotated[dict, Depends(require_user)]):
    accepted = application_state(request).operations.browser_issue(user['user_id'], payload)
    return envelope(request, dict(accepted=accepted))


@router.get('/related/{kind}/{identifier}', response_model=ApiEnvelope[RelatedOperation])
async def related(request: Request, admin: Admin, kind: RelatedKind,
                  identifier: Annotated[str, Path(pattern=r'^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$')]):
    response = await read(request, application_state(request).operations.repository.related, kind, identifier)
    if response['data'] is None:
        raise HTTPException(404, '关联业务记录不存在或已过保留期')
    return response
