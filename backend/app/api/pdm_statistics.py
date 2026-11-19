import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_admin
from app.api.runtime import runtime_state
from app.api.workloads import snapshot_requests
from app.core.request_cancellation import SqlRequestCancelled
from app.core.report_deadline import ReportOperationTimeout
from app.core.sql_capacity import SqlCapacityError
from app.database.sqlserver import SqlServerConfigurationError
from app.models.contracts import ApiEnvelope
from app.models.pdm_statistics import PdmBranchOptions, PdmOptionsQuery, PdmOverviewReport, PdmStatisticsQuery, PdmStatisticsReport
from app.services.pdm_statistics import PdmStatisticsService, PDM_STATISTICS_SCOPE
from app.services.report_snapshots import SnapshotRefreshBusy, SnapshotRefreshRejected, SnapshotRefreshFailed

router = APIRouter(prefix='/api/v1/pdm-statistics', tags=['pdm-statistics'], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


def service(request: Request) -> PdmStatisticsService:
    context = runtime_state(request)
    if not context.settings.file_database_enabled:
        raise HTTPException(status_code=503, detail='PDM 数据源未启用，请先配置切割文件库连接')
    current = context.shared_reports.get(PDM_STATISTICS_SCOPE)
    if not isinstance(current, PdmStatisticsService):
        raise HTTPException(status_code=503, detail='PDM 数据源未登记或不可用，请检查文件库绑定')
    return current


def query_error(exc: Exception) -> None:
    if isinstance(exc, SnapshotRefreshFailed):
        raise HTTPException(status_code=503, detail=str(exc), headers={'X-Report-Error': 'source_unavailable'}) from None
    if isinstance(exc, SnapshotRefreshBusy):
        raise HTTPException(status_code=503, detail=str(exc), headers={'Retry-After': '5', 'X-Report-Error': 'snapshot_refresh_busy'}) from None
    if isinstance(exc, SnapshotRefreshRejected):
        raise HTTPException(status_code=503, detail=str(exc), headers={'X-Report-Error': 'snapshot_queue_full'}) from None
    if isinstance(exc, (SqlRequestCancelled, ReportOperationTimeout, SqlCapacityError)):
        raise exc
    if isinstance(exc, SqlServerConfigurationError):
        raise HTTPException(status_code=503, detail='PDM 数据源不可用，请检查连接配置') from None
    logger.error('PDM统计查询失败 error_type=%s', type(exc).__name__)
    raise HTTPException(status_code=502, detail='PDM 查询失败，请检查连接及数据源查询日志') from None


@snapshot_requests.route(router.post('/options', response_model=ApiEnvelope[PdmBranchOptions]))
def options(payload: PdmOptionsQuery, request: Request, refresh: bool = False):
    current = service(request)
    try:
        data = current.options(payload, force=refresh)
    except Exception as exc:
        query_error(exc)
    return {'success': True, 'data': data, 'request_id': request.state.request_id}


@snapshot_requests.route(router.post('/report', response_model=ApiEnvelope[PdmStatisticsReport]))
def report(payload: PdmStatisticsQuery, request: Request, refresh: bool = False):
    current = service(request)
    try:
        data = current.report(payload, force=refresh)
    except Exception as exc:
        query_error(exc)
    return {'success': True, 'data': data, 'request_id': request.state.request_id}


@snapshot_requests.route(router.get('/overview', response_model=ApiEnvelope[PdmOverviewReport]))
def overview(request: Request, refresh: bool = False):
    current = service(request)
    try:
        data = current.overview(force=refresh)
    except Exception as exc:
        query_error(exc)
    return {'success': True, 'data': data, 'request_id': request.state.request_id}
