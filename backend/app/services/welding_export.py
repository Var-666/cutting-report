from __future__ import annotations

import csv
import io
from pathlib import Path
from tempfile import TemporaryDirectory

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import Settings
from app.models.welding import ReportType, WeldingQuery
from app.services.pdf_service import PdfService
from app.services.welding_service import WeldingService


REPORT_TITLES = {'daily': '每日生产统计', 'parts': '工件生产明细', 'jobs': '焊接作业明细', 'equipment': '设备与机器人生产统计'}
COMMON = [('device_no', '设备编号'), ('robot_no', '机器人编号')]
METRICS = [('involved_part_count', '参与工件数'), ('job_count', '作业次数'), ('flat_length_m', '平焊长度(m)'),
           ('vertical_length_m', '立焊长度(m)'), ('wrap_length_m', '包角类型作业长度(m)'),
           ('unknown_length_m', '未知工艺长度(m)'), ('weld_length_m', '焊接长度(m)'),
           ('working_hours', '累计作业用时(h)'), ('output_per_hour', '单位作业用时产出(m/h)')]
COLUMNS = {
    'daily': [('production_date', '生产日期'), *METRICS, ('completed_part_count', '完成工件数(待核实)'), ('arc_rate', '燃弧率(待核实)')],
    'equipment': [*COMMON, *METRICS],
    'jobs': [('id', '作业ID'), ('end_time', '作业结束时间'), ('serial_number', '工件流水号'), *COMMON,
             ('riser_no', '立板编号'), ('welding_seam_no', '焊缝序号'), ('weld_type_label', '焊接类型'),
             ('weld_length_m', '焊接长度(m)'), ('weld_speed', '源表焊接速度(mm/min)'), ('working_hours', '作业用时(h)'), ('state_label', '作业状态'), ('quality_reasons', '数据核查提示')],
    'parts': [('id', '工件ID'), ('project_no', '项目编号'), ('project_name', '项目名称'), ('section_no', '分段号'),
              ('part_no', '工件编号'), ('serial_number', '工件流水号'), ('device_no', '工件记录设备编号'),
              ('theoretical_length_m', '累计理论长度(m)'), ('actual_length_m', '累计实际长度(m)'),
              ('weld_rate', '源表焊接率(待核实)'), ('arc_rate', '源表燃弧率(待核实)'),
              ('start_time', '开始时间'), ('end_time', '结束时间'), ('state_label', '工件状态'), ('quality_reasons', '数据核查提示')],
    'seams': [('id', '焊缝ID'), ('riser_no', '立板编号'), ('welding_seam_no', '焊缝序号'), *COMMON,
              ('theoretical_length_m', '理论长度(m)'), ('actual_length_m', '实际长度(m)'),
              ('working_hours', '焊缝用时(h)'), ('start_time', '开始时间'), ('end_time', '结束时间'), ('state_label', '状态'), ('quality_reasons', '数据核查提示')],
}


def csv_cell(value):
    if value is None:
        return '待核实/未记录'
    if isinstance(value, list):
        value = '；'.join(map(str, value))
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def csv_bytes(columns: list[tuple[str, str]], rows: list[dict], metadata: list[tuple[str, str]]) -> bytes:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerows([[csv_cell(value) for value in pair] for pair in metadata])
    writer.writerow([label for _, label in columns])
    writer.writerows([[csv_cell(row.get(key)) for key, _ in columns] for row in rows])
    return ('\ufeff' + output.getvalue()).encode('utf-8')


class WeldingExport:
    def __init__(self, settings: Settings, service: WeldingService, pdf: PdfService) -> None:
        self.settings, self.service, self.pdf = settings, service, pdf

    def generate(self, kind: ReportType, query: WeldingQuery, format: str) -> tuple[bytes, str]:
        content, media = self.prepare(kind, query, format)
        return self.render_pdf(content) if format == 'pdf' else (content, media)

    def prepare(self, kind: ReportType, query: WeldingQuery, format: str) -> tuple[bytes, str]:
        limit = 100000 if format == 'csv' else 5000
        report = self.service.report(kind, query, export_limit=limit + 1)
        if report['total'] > limit or len(report['items']) > limit:
            raise ValueError(f'当前筛选结果超过{limit:,}行，请缩小日期或筛选范围后导出')
        applied = report['query']
        filters = [f'日期：{applied["start"]} 至 {applied["end"]}']
        for key, label in [('device_no', '设备'), ('robot_no', '机器人'), ('weld_type', '工艺代码'), ('state', '作业状态代码'),
                           ('part_state', '工件状态代码'), ('project_no', '项目'), ('section_no', '分段'), ('part_no', '工件编号'), ('serial_number', '流水号')]:
            if applied.get(key) is not None and applied.get(key) != '':
                filters.append(f'{label}：{applied[key]}')
        if applied.get('anomalies_only'):
            filters.append('仅异常候选记录')
        metadata = [('报表', REPORT_TITLES[kind]), ('数据来源', '小组立焊接线 / MySQL'),
                    ('筛选条件', '；'.join(filters) + '；导出全部筛选结果'), ('生成时间', report['meta']['calculated_at']),
                    ('生产时区', report['meta']['timezone']), ('数据更新时间', str(report['meta']['data_updated_at'])),
                    ('统计说明', '长度单位m；按结束时间归属；工件长度为累计值；未知状态待核实')]
        if format == 'csv':
            return csv_bytes(COLUMNS[kind], report['items'], metadata), 'text/csv; charset=utf-8'
        environment = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / 'templates'), autoescape=select_autoescape(['html', 'j2']))
        chart_path = self.settings.project_root / 'vendor' / 'echarts.min.js'
        html = environment.get_template('welding_report.html.j2').render(
            title=REPORT_TITLES[kind], columns=COLUMNS[kind], report=report, metadata=metadata,
            echarts=chart_path.read_text(encoding='utf-8') if chart_path.is_file() else '',
        )
        return html.encode('utf-8'), 'text/html; charset=utf-8'

    def render_pdf(self, html: bytes) -> tuple[bytes, str]:
        with TemporaryDirectory(prefix='welding-report-') as folder:
            path = Path(folder) / 'report.html'
            path.write_bytes(html)
            return self.pdf.render(path).read_bytes(), 'application/pdf'
