"""Isolated report content for screen, PDF and email rendering checks."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.models.domain import to_primitive
from app.models.designer import TemplateConfig
from app.modules.welding.models import WeldingQuery
from app.services.alarm_report_service import AlarmReportService
from app.services.chart_service import ChartService
from app.services.custom_report_service import CustomReportService
from app.services.leadership_report_service import LeadershipReportService
from app.services.pdf_service import PdfService
from app.services.report_closing_comparison import closing_comparison
from app.services.report_service import ReportService
from app.modules.welding.export import WeldingExport
from tests.line_fixtures import cutting_settings, welding_settings


STYLE_KINDS = ['leadership', 'workshop', 'equipment', 'custom', 'alarm',
               'welding-daily', 'welding-jobs', 'welding-parts', 'welding-equipment']
BUSINESS_MARKER = '完整业务内容校验标记'


def style_report_html(kind: str, directory: Path, *, energy_connected: bool | None = None) -> Path:
    root = Path(__file__).resolve().parents[2]
    cfg = cutting_settings(project_root=root, data_source_mode='demo', report_max_detail_rows=1)
    renderer = ReportService(cfg)
    analysis = dict(mode='model', status='success', overview='本期生产记录已汇总。', features='统计覆盖所选日期。',
                    alerts='无设备开机时长，不推断利用率。', suggestions='核对生产来源与工程归属。', facts=[], cached=False)
    if kind.startswith('welding-'):
        weld_kind = kind.removeprefix('welding-')
        query = WeldingQuery(start=date(2026, 11, 1), end=date(2026, 11, 13), device_no=0)
        summary = dict(job_count=1, involved_part_count=1, working_hours=2.5, weld_length_m=125.6)
        row = dict(id=1, production_date='2026-11-12', end_time='2026-11-12 10:00:00', start_time='2026-11-12 08:00:00',
                   serial_number=BUSINESS_MARKER, project_no='PROJECT-001', project_name='长工程名称用于确认导出内容不被裁切',
                   section_no='P01', part_no='PART-001', device_no=0, robot_no=0, riser_no=1, welding_seam_no=1,
                   weld_type_label='平焊包角', weld_length_m=125.6, theoretical_length_m=130, actual_length_m=125.6,
                   working_hours=2.5, weld_speed=550, state_label='已完成', quality_reasons=['请核对关联记录'])
        payload = dict(query=query.model_dump(mode='json'), total=1, items=[row], summary=summary,
                       daily=[dict(production_date='2026-11-12', **summary)], data_version='a' * 64,
                       meta=dict(calculated_at='2026-11-13 11:00:00', timezone='Asia/Shanghai', data_updated_at='2026-11-12 10:00:00',
                                 notices=['仅使用已记录的焊接生产数据。'],
                                 quality={'invalid_length_rows': 1, 'invalid_duration_rows': 0}, unlinked_part_count=0))
        if weld_kind in {'daily', 'equipment'}:
            payload['items'][0]['device_no'] = BUSINESS_MARKER if weld_kind == 'equipment' else 0
        if weld_kind == 'daily':
            analysis.update(data_version=payload['data_version'], overview=BUSINESS_MARKER)
        instance = SimpleNamespace(report=lambda *a, **kw: payload, analyze_report=lambda *a, **kw: analysis)
        html = WeldingExport(welding_settings(project_root=root), instance, PdfService()).prepare(weld_kind, query, 'html')[0].decode()
    elif kind == 'alarm':
        payload = dict(summary='发现需要现场核查的告警记录。', overall_risk='critical',
                       facts=[dict(id='A001', machine_no='20KW切割机', severity='critical', alarm_code='E001',
                                   alarm_class='运行提示', alarm_desc=BUSINESS_MARKER + '：核对当前状态与现场记录。',
                                   start_time='2026-11-12 10:00:00', status='open')],
                       findings=[dict(severity='high', title='现场状态核查', analysis='结合告警发生时间核查状态。',
                                      possible_causes=['需结合现场记录确认'], confidence='中', evidence_ids=['A001'])],
                       actions=[dict(priority=1, action='由现场授权人员核实设备状态。', evidence_ids=['A001'])],
                       limitations=['本报告不替代现场安全规程。'])
        html = AlarmReportService(cfg, PdfService()).environment.get_template('alarm_report.html.j2').render(
            report_id='STYLE-ALARM', machine_no='20KW切割机', analysis=payload, generated_at='2026-11-13 11:00:00',
            cover=renderer.environment.globals['analysis_cover'](), demo_mode=True,
            severity_labels={'warning': '提示', 'high': '高', 'critical': '严重'})
    else:
        totals = dict(task_count=1, cut_length_m=125.6, groove_length_m=10.5, work_time_seconds=3600)
        chart = dict(animation=False, xAxis=dict(type='category', data=['11-12']), yAxis=dict(type='value'),
                     series=[dict(type='bar', data=[125.6], name='生产长度')])
        record = dict(task_id=BUSINESS_MARKER, project_code='PROJECT-001', ship_no='生产船名', segment_name='P01',
                      program_no='长程序文件名-' + '完整内容' * 12, cut_length=125.6, groove_length=10.5, work_time=3600,
                      production_time='2026-11-12T10:00:00', electricity_consumption=None, gas_consumption=None,
                      confirmed_by='生产确认人员', remark='不展示的备注')
        payload = dict(meta=dict(report_type=kind, title={'leadership': '切割生产领导报表', 'workshop': '切割生产车间日报',
                            'equipment': '切割生产设备报表', 'custom': '自定义生产报表'}[kind], report_id=f'STYLE-{kind}',
                            report_date='2026-11-12', period_start='2026-11-12', period_end='2026-11-12',
                            generated_at='2026-11-13T11:00:00+08:00', system_version='test', custom_period=True),
                       filters=dict(project_code=None, ship_no=None), analysis=analysis,
                       summary=dict(task_count=1, total_cut_length=125.6, total_groove_length=10.5, total_work_time=3600,
                                    avg_cut_length=125.6, avg_groove_length=10.5, avg_work_time=3600),
                       workshop_records=[record], worker_summaries=[],
                       equipment_summary=dict(task_count=1, total_work_time=3600, total_electricity_consumption=None,
                                              total_gas_consumption=None, electricity_record_count=0, gas_record_count=0),
                       equipment_records=[dict(record, ship_no=BUSINESS_MARKER)], units=dict(electricity='kWh', gas='m³'),
                       energy_month=dict(rows=[], total_task_count=1, total_work_time_seconds=3600,
                                         summary=dict(electricity=dict(value=None), gas=dict(value=None))),
                       charts={key: chart for key in ['length_combo', 'equipment_detail', 'equipment_month']})
        if kind in {'leadership', 'workshop', 'equipment'}:
            if kind != 'leadership':
                payload[f'{kind}_records'][0]['project_code'] = None
            closing_totals = {key: Decimal(str(value)) for key, value in totals.items()}
            if kind == 'equipment':
                closing_totals.update(electricity=8, gas=4)
            selected = date(2026, 11, 12)
            comparison = closing_comparison(selected, selected, datetime(2026, 11, 12, 18), cfg.timezone,
                lambda windows: dict.fromkeys(windows, closing_totals),
                grouping='month_quarter_year' if kind == 'leadership' else 'day_week_month')
            if kind == 'leadership':
                payload['daily_production'], *comparison = comparison
            if kind == 'equipment':
                for column in comparison:
                    hours = round((column['end'] - column['start']).total_seconds() / 3600)
                    column['meter_coverage'] = {name: dict(value=closing_totals[name], valid_count=hours, expected_count=hours)
                                                for name in ('electricity', 'gas')}
            payload['closing_comparison'] = comparison
        template = 'report.html.j2'
        if kind == 'leadership':
            leader = LeadershipReportService(cfg).fetch(date(2026, 11, 1))
            payload.update(leadership=to_primitive(leader), charts=ChartService().build_leadership(leader))
            analysis['facts'] = [
                dict(id='F001', label='当月任务数', value='100 项'),
                dict(id='F006', label='工程号与任务较多的日期', value='工程 P01'),
            ]
            analysis['brief'] = dict(
                headline='领导简报结论', summary='优先核对工程归属。\n确认后更新汇总。', scope={}, data_quality=[],
                findings=[dict(candidate_id='C_project', title='工程任务分布', detail=BUSINESS_MARKER)], actions=[])
        elif kind == 'custom':
            query = dict(name='产量查询', query_task_id='q', version=1,
                         columns=[dict(field='name', label='业务名称', type='text'), dict(field='amount', label='产量', type='number')])
            definition = dict(name='自定义生产', description='按配置统计生产记录', visual_config=dict(
                metrics=[dict(field='amount', label='总产量', aggregation='sum', unit='项')],
                chart=dict(category_field='name', value_fields=['amount'])))
            template_meta = dict(name='生产报表模板', template_id='t', version=1, config=TemplateConfig(layout='table_first').model_dump())
            payload.update(definition=definition, query_task=query, template_meta=template_meta,
                custom=CustomReportService(cfg).build_report_data(definition=definition, query_task=query,
                    template=template_meta, rows=[dict(name=BUSINESS_MARKER, amount=125.6), dict(name='超出明细上限', amount=42)]))
            template = 'custom_report.html.j2'
        elif kind == 'workshop':
            payload['data_quality'] = [dict(level='error', task_id='TASK-001', message='发现负数数据；对应字段不计入统计。')]
            analysis['brief'] = dict(
                headline='本期生产记录已汇总', summary='切割工时存在需要复核的记录。', scope={}, data_quality=[],
                findings=[dict(candidate_id='C_production', title='本期实际切割记录', detail='按有效任务汇总。'),
                          dict(candidate_id='C_duration', title='切割时间较长的任务', detail='建议核对任务起止时间。')],
                actions=[dict(title='核对任务工时', detail='核对时间记录与现场生产安排。')])
        elif kind == 'equipment':
            payload['equipment_summary'].update(total_electricity_consumption=30, electricity_record_count=1)
            payload['equipment_records'][0]['electricity_consumption'] = 30
        html = renderer.environment.get_template(template).render(report=payload,
            echarts_source=cfg.echarts_vendor_path.read_text(), demo_mode=True, device_name='20KW切割机',
            energy_connected=kind == 'equipment' if energy_connected is None else energy_connected)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{kind}.html'
    path.write_text(html, encoding='utf-8')
    return path
