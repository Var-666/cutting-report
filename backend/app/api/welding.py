from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError

from app.api.auth import require_admin
from app.api.runtime import runtime_state
from app.services.production_lines import SERVICE_LINES, resolve_line_service
from app.services.report_snapshots import SnapshotRefreshBusy, SnapshotRefreshRejected
from app.api.workloads import RequestWorkload, analysis_requests, pdf_requests
from app.config import settings
from app.models.domain import to_primitive
from app.models.welding import WeldingAnalysisRequest, WeldingQuery
from app.database.welding import WeldingUnavailable
from app.services.analysis_execution import analysis_operation
from app.services.llm_service import LlmService
from app.services.pdf_service import PdfRenderError
from app.services.request_cancellation import SqlRequestCancelled
from app.services.welding_export import WeldingExport, csv_bytes, COLUMNS


router = APIRouter(prefix='/api/v1/welding', tags=['welding'], dependencies=[Depends(require_admin)])
# Explicit smaller connection pools also lower execution capacity after restart.
welding_requests = RequestWorkload(max(1, min(4, settings.weld_db_pool_size)), '焊接数据查询', max_waiters=12, queue_timeout_seconds=5)


def service(request: Request):
    selected = request.headers.get('X-Production-Line')
    line = next((item for item in SERVICE_LINES if item['type'] == 'welding' and (not selected or item['id'] == selected)), None)
    if line is None:
        raise HTTPException(status_code=422, detail='焊接产线上下文无效')
    instance = resolve_line_service(runtime_state(request), line['id'])
    if instance is None or not instance.settings.weld_enabled:
        raise HTTPException(status_code=503, detail=f'{line["name"]}尚未启用')
    return instance


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


@welding_requests.route(router.get('/health'))
def health(request: Request):
    with source_errors():
        return response(request, service(request).health())


@welding_requests.route(router.get('/dashboard'))
def dashboard(request: Request):
    query = parse_query(request)
    with source_errors():
        return response(request, service(request).dashboard(query, **({'force': True} if request.query_params.get('refresh') == 'true' else {})))


@welding_requests.route(router.get('/statistics/daily'))
def daily(request: Request):
    query = parse_query(request)
    with source_errors():
        return response(request, service(request).page('daily', query, **({'force': True} if request.query_params.get('refresh') == 'true' else {})))


@welding_requests.route(router.get('/parts'))
def parts(request: Request):
    query = parse_query(request)
    with source_errors():
        return response(request, service(request).page('parts', query, **({'force': True} if request.query_params.get('refresh') == 'true' else {})))


@welding_requests.route(router.get('/jobs'))
def jobs(request: Request):
    query = parse_query(request)
    with source_errors():
        return response(request, service(request).page('jobs', query, **({'force': True} if request.query_params.get('refresh') == 'true' else {})))


@welding_requests.route(router.get('/equipment/summary'))
def equipment(request: Request):
    query = parse_query(request)
    with source_errors():
        return response(request, service(request).page('equipment', query, **({'force': True} if request.query_params.get('refresh') == 'true' else {})))


@welding_requests.route(router.get('/parts/{part_id}'))
@welding_requests.route(router.get('/parts/{part_id}/seams'))
def part(request: Request, part_id: int, page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100)):
    with source_errors():
        data = service(request).part(part_id, page, page_size)
    if data is None:
        raise HTTPException(status_code=404, detail='工件不存在')
    return response(request, data)


@welding_requests.route(router.get('/parts/{part_id}/seams/export'))
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


@router.get('/export')
async def export(request: Request, report_type: Literal['daily', 'parts', 'jobs', 'equipment'], format: Literal['csv', 'html', 'pdf'] = 'csv'):
    query = parse_query(request)
    instance = service(request)
    exporter = WeldingExport(instance.settings, instance, request.app.state.pdf_service)
    with source_errors():
        content, media = await welding_requests.run_sync(exporter.prepare, report_type, query, format, _request=request)
        if format == 'pdf':
            content, media = await pdf_requests.run_sync(exporter.render_pdf, content)
    return Response(content, media_type=media, headers={'Content-Disposition': f'attachment; filename="welding-{report_type}-{query.start}-{query.end}.{format}"'})


@router.post('/analysis')
async def analysis(request: Request, payload: WeldingAnalysisRequest):
    instance = service(request)
    with source_errors():
        report = await welding_requests.run_sync(instance.page, payload.report_type, payload.query, _request=request)
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
