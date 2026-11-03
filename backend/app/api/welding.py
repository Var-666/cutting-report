from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Literal, cast
from time import monotonic
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError

from app.api.auth import require_admin
from app.api.runtime import application_state, runtime_state
from app.services.production_lines import resolve_line_service
from app.services.report_snapshots import SnapshotRefreshBusy, SnapshotRefreshRejected
from app.api.workloads import RequestWorkload, analysis_requests, pdf_requests
from app.config import settings
from app.models.domain import to_primitive
from app.models.welding import WeldingAnalysisRequest, WeldingQuery
from app.models.contracts import ApiEnvelope
from app.models.report_contracts import ReportAnalysis
from app.models.welding_contracts import WeldingDailyReport, WeldingJobsReport, WeldingPartsReport, WeldingEquipmentReport, WeldingPart, WeldingHealth
from app.database.welding import WeldingUnavailable
from app.services.analysis_execution import analysis_operation
from app.services.llm_service import LlmService
from app.services.pdf_service import PdfRenderError
from app.core.request_cancellation import SqlRequestCancelled
from app.services.welding_export import WeldingExport, csv_bytes, COLUMNS
from app.services.welding_service import WeldingService


logger = logging.getLogger(__name__)


router = APIRouter(prefix='/api/v1/welding', tags=['welding'], dependencies=[Depends(require_admin)])
# Global execution capacity is unchanged; each bound source enforces its own pool limit.
welding_requests = RequestWorkload(4, '焊接数据查询', max_waiters=12, queue_timeout_seconds=5)
# SQLite-only reads must not queue behind source exports/drilldowns. Source fallbacks retain the MySQL limit.
snapshot_requests = RequestWorkload(4, '焊接快照读取', max_waiters=12, queue_timeout_seconds=5)

# Keep parse_query's duplicate/unknown-key checks; describe its existing model without a second parser.
_query_schema = WeldingQuery.model_json_schema()
WELD_QUERY_PARAMETERS = [
    {'name': name, 'in': 'query', 'required': name in _query_schema.get('required', []), 'schema': schema}
    for name, schema in _query_schema['properties'].items()
]
WELD_REPORT_QUERY = {'parameters': [*WELD_QUERY_PARAMETERS,
    {'name': 'refresh', 'in': 'query', 'required': False, 'schema': {'type': 'boolean', 'default': False}}]}


def service(request: Request) -> WeldingService:
    selected = request.headers.get('X-Production-Line')
    if not selected:
        raise HTTPException(status_code=422, detail='必须明确指定焊接产线')
    try:
        instance = resolve_line_service(runtime_state(request), selected)
    except ValueError:
        raise HTTPException(status_code=503, detail='焊接产线未登记或已停用') from None
    if runtime_state(request).catalog.get(selected).type != "welding":
        raise HTTPException(status_code=403, detail='当前产线不支持焊接业务')
    return cast(WeldingService, instance)


@contextmanager
def source_errors():
    try:
        yield
    except SnapshotRefreshBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={'Retry-After': '1', 'X-Welding-Error': 'snapshot_refresh_busy'}) from None
    except SnapshotRefreshRejected as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={'X-Welding-Error': 'snapshot_queue_full'}) from None
    except WeldingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={'X-Welding-Error': exc.category}) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except PdfRenderError:
        raise HTTPException(status_code=503, detail='PDF生成失败或服务繁忙，请重试或导出HTML') from None


def parse_query(request: Request) -> WeldingQuery:
    ignored = {'format', 'report_type', 'refresh'}
    values = {}
    for key in request.query_params:
        entries = request.query_params.getlist(key)
        if len(entries) != 1:
            raise HTTPException(status_code=422, detail='查询参数不允许重复')
        if key == 'refresh' and entries[0] not in ('true', 'false'):
            raise HTTPException(status_code=422, detail='刷新参数无效')
        if key not in ignored:
            values[key] = entries[0]
    try:
        return WeldingQuery.model_validate(values)
    except ValidationError:
        raise HTTPException(status_code=422, detail='日期、分页或筛选参数无效，请检查查询条件') from None


def response(request: Request, data) -> dict:
    return {'success': True, 'data': to_primitive(data), 'request_id': getattr(request.state, 'request_id', '')}


@welding_requests.route(router.get('/health', response_model=None, responses={200: {'model': ApiEnvelope[WeldingHealth]}}))
def health(request: Request):
    with source_errors():
        return response(request, service(request).health())


async def _read_page(instance: WeldingService, kind: str, query: WeldingQuery, request: Request, *, force: bool = False):
    snapshots = instance.snapshot_service
    pool = snapshot_requests if snapshots is not None and snapshots.coordination is not None else welding_requests
    if kind == 'dashboard':
        return await pool.run_sync(instance.dashboard, query, **({'force': True} if force else {}), _request=request)
    return await pool.run_sync(instance.page, kind, query, **({'force': True} if force else {}), _request=request)


async def _page_response(request: Request, kind: str):
    started = monotonic()
    query = None
    instance = None
    try:
        query = parse_query(request)
        instance = service(request)
        with source_errors():
            data = await _read_page(instance, kind, query, request, force=request.query_params.get('refresh') == 'true')
        return response(request, data)
    finally:
        logger.info('welding page request line=%s report_type=%s start=%s end=%s request_id=%s elapsed_ms=%d',
                    instance.settings.device_id if instance else None, kind,
                    query.start if query else None, query.end if query else None,
                    getattr(request.state, 'request_id', None), round((monotonic() - started) * 1000))


@router.get('/dashboard', response_model=None, responses={200: {'model': ApiEnvelope[WeldingJobsReport]}}, openapi_extra=WELD_REPORT_QUERY)
async def dashboard(request: Request):
    return await _page_response(request, 'dashboard')


@router.get('/statistics/daily', response_model=None, responses={200: {'model': ApiEnvelope[WeldingDailyReport]}}, openapi_extra=WELD_REPORT_QUERY)
async def daily(request: Request):
    return await _page_response(request, 'daily')


@router.get('/parts', response_model=None, responses={200: {'model': ApiEnvelope[WeldingPartsReport]}}, openapi_extra=WELD_REPORT_QUERY)
async def parts(request: Request):
    return await _page_response(request, 'parts')


@router.get('/jobs', response_model=None, responses={200: {'model': ApiEnvelope[WeldingJobsReport]}}, openapi_extra=WELD_REPORT_QUERY)
async def jobs(request: Request):
    return await _page_response(request, 'jobs')


@router.get('/equipment/summary', response_model=None, responses={200: {'model': ApiEnvelope[WeldingEquipmentReport]}}, openapi_extra=WELD_REPORT_QUERY)
async def equipment(request: Request):
    return await _page_response(request, 'equipment')


@welding_requests.route(router.get('/parts/{part_id}', response_model=None, responses={200: {'model': ApiEnvelope[WeldingPart]}}))
@welding_requests.route(router.get('/parts/{part_id}/seams', response_model=None, responses={200: {'model': ApiEnvelope[WeldingPart]}}))
def part(request: Request, part_id: int, page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100)):
    with source_errors():
        data = service(request).part(part_id, page, page_size)
    if data is None:
        raise HTTPException(status_code=404, detail='工件不存在')
    return response(request, data)


@welding_requests.route(router.get('/parts/{part_id}/seams/export', response_class=Response, responses={200: {'content': {'text/csv': {'schema': {'type': 'string', 'format': 'binary'}}}}}))
def seam_export(request: Request, part_id: int):
    instance = service(request)
    with source_errors():
        with instance.repository.client.snapshot() as session:
            raw = instance.repository.part(session, part_id)
            data = None if raw is None else instance.repository.seams(session, raw, 1, 100001)
    if data is None:
        raise HTTPException(status_code=404, detail='工件不存在')
    rows, total, ambiguous = data
    if ambiguous:
        raise HTTPException(status_code=409, detail='工件关联待核实，暂不能导出焊缝')
    if total > 100000:
        raise HTTPException(status_code=422, detail='焊缝超过100,000行，请缩小范围')
    from app.services.welding_service import mapped_row
    content = csv_bytes(COLUMNS['seams'], [mapped_row(row, 'seams') for row in rows],
                        [('数据来源', '小组立焊接线'), ('工件ID', str(part_id)),
                         ('生成时间', datetime.now(ZoneInfo(instance.settings.weld_db_timezone)).isoformat()),
                         ('工件更新时间', str(raw.get('update_date') or '未记录')), ('单位', '长度m，用时h；状态待核实')])
    return Response(content, media_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="welding-seams-{part_id}.csv"'})


@router.get('/export', response_class=Response, responses={200: {'content': {media: {'schema': {'type': 'string', 'format': 'binary'}} for media in ('text/csv', 'text/html', 'application/pdf')}}}, openapi_extra={'parameters': WELD_QUERY_PARAMETERS})
async def export(request: Request, report_type: Literal['daily', 'parts', 'jobs', 'equipment'], format: Literal['csv', 'html', 'pdf'] = 'csv'):
    query = parse_query(request)
    instance = service(request)
    exporter = WeldingExport(instance.settings, instance, application_state(request).pdf_service)
    with source_errors():
        content, media = await welding_requests.run_sync(exporter.prepare, report_type, query, format, _request=request)
        if format == 'pdf':
            content, media = await pdf_requests.run_sync(exporter.render_pdf, content)
    return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="welding-{report_type}-{query.start}-{query.end}.{format}"'})


@router.post('/analysis', response_model=None, responses={200: {'model': ApiEnvelope[ReportAnalysis]}})
async def analysis(request: Request, payload: WeldingAnalysisRequest):
    instance = service(request)
    with source_errors():
        report = await _read_page(instance, payload.report_type, payload.query, request)
    return response(request, await analysis_requests.run_sync(_analyze, instance, report, payload))


def _analyze(instance, report: dict, payload: WeldingAnalysisRequest):
    if report['data_version'] != payload.data_version:
        raise HTTPException(status_code=409, detail='报表数据已更新，请刷新后重新生成分析')
    if not instance.settings.llm_enabled:
        raise HTTPException(status_code=503, detail='智能分析模型尚未启用，常规报表可继续使用')
    cached = instance.cached_analysis(report['data_version'])
    if cached is not None:
        return {**to_primitive(cached), 'cached': True}
    context = {'report_date': f'{payload.query.start} 至 {payload.query.end}',
               'facts': [{'label': key, 'value': value} for key, value in report['summary'].items()],
               'tables': [{'name': '按日汇总', 'rows': report['daily']}, {'name': '设备机器人工位', 'rows': report['equipment']},
                          {'name': '统计口径和数据质量', 'rows': [report['meta']]}]}
    try:
        with analysis_operation(instance.settings, 'welding', context['report_date']):
            result = LlmService(instance.settings).analyze_context('welding', context)
    except SqlRequestCancelled:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail='智能分析暂不可用，请稍后重试；常规报表不受影响') from None
    result.data_version = report['data_version']
    result.generated_at = report['meta']['calculated_at']
    instance.remember_analysis(report['data_version'], result)
    return result
