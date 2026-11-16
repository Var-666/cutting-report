import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_admin
from app.api.runtime import runtime_state
from app.api.workloads import query_requests
from app.core.request_cancellation import SqlRequestCancelled
from app.core.report_deadline import ReportOperationTimeout
from app.core.sql_capacity import SqlCapacityError
from app.database.pdm_statistics import PdmStatisticsRepository
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.models.contracts import ApiEnvelope
from app.models.pdm_statistics import PdmBranchOptions, PdmOptionsQuery, PdmStatisticsQuery, PdmStatisticsReport
from app.services.pdm_statistics import PdmStatisticsService

router = APIRouter(prefix='/api/v1/pdm-statistics', tags=['pdm-statistics'], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


def service(request: Request) -> PdmStatisticsService:
    context = runtime_state(request)
    if not context.settings.file_database_enabled:
        raise HTTPException(status_code=503, detail='PDM 数据源未启用，请先配置切割文件库连接')
    try:
        client = context.database_sources.client(context.settings.file_source_key)
    except (ValueError, KeyError):
        raise HTTPException(status_code=503, detail='PDM 数据源未登记或不可用，请检查文件库绑定') from None
    if not isinstance(client, SqlServerClient):
        raise HTTPException(status_code=503, detail='PDM 需要绑定 SQL Server 数据源')
    return PdmStatisticsService(PdmStatisticsRepository(client))


def query_error(exc: Exception) -> None:
    if isinstance(exc, (SqlRequestCancelled, ReportOperationTimeout, SqlCapacityError)):
        raise exc
    if isinstance(exc, SqlServerConfigurationError):
        raise HTTPException(status_code=503, detail='PDM 数据源不可用，请检查连接配置') from None
    logger.error('PDM统计查询失败 error_type=%s', type(exc).__name__)
    raise HTTPException(status_code=502, detail='PDM 查询失败，请检查连接及数据源查询日志') from None


@query_requests.route(router.post('/options', response_model=ApiEnvelope[PdmBranchOptions]))
def options(payload: PdmOptionsQuery, request: Request):
    current = service(request)
    try:
        data = current.options(payload)
    except Exception as exc:
        query_error(exc)
    return {'success': True, 'data': data, 'request_id': request.state.request_id}


@query_requests.route(router.post('/report', response_model=ApiEnvelope[PdmStatisticsReport]))
def report(payload: PdmStatisticsQuery, request: Request):
    current = service(request)
    try:
        data = current.report(payload)
    except Exception as exc:
        query_error(exc)
    return {'success': True, 'data': data, 'request_id': request.state.request_id}
