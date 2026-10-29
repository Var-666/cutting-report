"""Workshop business workflows; HTTP and persistence are supplied by callers."""
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Literal
from app.models.domain import to_primitive, production_day
from app.models.workshop_requests import (
    ConfirmTaskRequest, ConfirmSnapshotTaskRequest, ConfirmTasksRequest,
    RevokeTaskRequest, CorrectTaskRequest,
)
from app.services.statistics_service import build_top_cut_length_tasks, build_worker_work_summaries, build_workshop_task_distributions
from app.services.report_analysis_service import analysis_period_label, workshop_analysis_source_version
from app.services.workshop_sorting import sort_workshop_by_recency


class WorkshopTaskNotFoundError(ValueError):
    pass


def _workshop_analysis_source_version(report_date: str, records: list) -> str:
    return workshop_analysis_source_version(report_date, records)

def _workshop_trend(records: list) -> list[dict]:
    """Aggregate the filtered report rows into truthful daily KPI samples."""
    days: dict[str, list] = defaultdict(list)
    for row in records:
        stamp = production_day(row)
        if stamp is None:
            continue
        days[stamp.isoformat()].append(row)
    result = []
    for stamp, rows in sorted(days.items()):
        task_count = len(rows)
        confirmed_count = sum(bool(getattr(row, "confirmed_by", None)) for row in rows)
        project_count = len(
            {
                row.project_code
                for row in rows
                if getattr(row, "project_code", None)
            }
        )
        result.append(
            {
                "report_date": stamp,
                "task_count": task_count,
                "pending_count": task_count - confirmed_count,
                "confirmed_count": confirmed_count,
                "project_count": project_count,
                "confirmation_rate": (
                    round(confirmed_count / task_count * 100, 1) if task_count else 0
                ),
            }
        )
    return result


class WorkshopService:
    def __init__(self, reports, confirmations, settings, *, query_service=None, range_report_service=None):
        self.reports = reports
        self.confirmations = confirmations
        self.settings = settings
        self.query_service = query_service
        self.range_report_service = range_report_service

    def _records_for_date(self, report_date: date):
        report, records = self.reports.get_daily_report_records(report_date.isoformat())
        if report and report.get('scope_machine_no') != self.settings.report_machine_no:
            return None, []
        return report, records

    def latest_production_date(self, on_or_before: date, user: dict):
        latest = self.query_service.latest_production_date(on_or_before)
        return {'report_date': latest.isoformat() if latest else None}

    def generate_daily_report(self, payload: ConfirmTaskRequest, user: dict):
        bundle = self.query_service.prepare_workshop_data(payload.report_date)
        now = datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec='seconds')
        report = self.reports.sync_daily_report(report_date=payload.report_date.isoformat(), source_mode=self.settings.effective_data_source_mode, records=bundle.workshop_records, user=user, now=now, scope_machine_no=self.settings.report_machine_no)
        return report

    def list_tasks(self, report_date: date, project_name: str | None, confirmation_status: Literal['all', 'confirmed', 'unconfirmed'], sort_mode: Literal['manager', 'worker'], page: int, page_size: int, warmup: bool, user: dict):
        report, records = self._records_for_date(report_date)
        if warmup and (report is None or not report['item_count']):
            self.query_service.prepare_workshop_data(report_date)
        if report is None:
            return {
                'report': None,
                'items': [],
                'page': 1,
                'page_size': page_size,
                'total': 0,
                'project_names': [],
                'confirmed_count': 0,
                'worker_summaries': [],
                'duration_distribution': [],
                'status_distribution': [],
                'top_cut_length_tasks': [],
                'analysis_source_version': workshop_analysis_source_version(report_date.isoformat(), []),
                'current_user': user,
            }
        analysis_source_version = _workshop_analysis_source_version(report_date.isoformat(), records)
        project_names = sorted({row.project_code for row in records if row.project_code})
        if project_name:
            records = [row for row in records if row.project_code == project_name]
        confirmed_count = sum((1 for row in records if row.confirmed_by))
        worker_summaries = build_worker_work_summaries(records) if user.get('role') == 'admin' else []
        if confirmation_status == 'confirmed':
            records = [row for row in records if row.confirmed_by]
        elif confirmation_status == 'unconfirmed':
            records = [row for row in records if not row.confirmed_by]
        distributions = build_workshop_task_distributions(records)
        top_cut_length_tasks = build_top_cut_length_tasks(records)
        records = sort_workshop_by_recency(records, self.settings.timezone)
        total = len(records)
        start = (page - 1) * page_size
        items = to_primitive(records[start:start + page_size])
        return {
            'report': report,
            'items': items,
            'page': page,
            'page_size': page_size,
            'total': total,
            'project_names': project_names,
            'confirmed_count': confirmed_count,
            'worker_summaries': to_primitive(worker_summaries),
            **distributions,
            'top_cut_length_tasks': to_primitive(top_cut_length_tasks),
            'analysis_source_version': analysis_source_version,
            'current_user': user,
        }

    def list_range_tasks(self, start_date: date, end_date: date, project_name: str | None, confirmation_status: Literal['all', 'confirmed', 'unconfirmed'], page: int, page_size: int):
        bundle, records = self.range_report_service.workshop_records(start_date, end_date)
        analysis_source_version = _workshop_analysis_source_version(analysis_period_label(start_date, end_date), records)
        project_names = sorted({row.project_code for row in records if row.project_code})
        if project_name:
            records = [row for row in records if row.project_code == project_name]
        worker_summaries = build_worker_work_summaries(records)
        if confirmation_status == 'confirmed':
            records = [row for row in records if row.confirmed_by]
        elif confirmation_status == 'unconfirmed':
            records = [row for row in records if not row.confirmed_by]
        distributions = build_workshop_task_distributions(records)
        top_cut_length_tasks = build_top_cut_length_tasks(records)
        trend = _workshop_trend(records)
        records = sort_workshop_by_recency(records, self.settings.timezone)
        total = len(records)
        start = (page - 1) * page_size
        visible = records[start:start + page_size]
        return {
            'report': {'report_date': start_date.isoformat(), 'period_start': start_date.isoformat(), 'period_end': end_date.isoformat(), 'scope_machine_no': self.settings.report_machine_no, 'item_count': total, 'confirmed_count': sum((bool(row.confirmed_by) for row in records)), 'generated_at': to_primitive(bundle.data_updated_at)},
            'items': to_primitive(visible),
            'total': total,
            'page': page,
            'page_size': page_size,
            'project_names': project_names,
            'confirmed_count': sum((bool(row.confirmed_by) for row in records)),
            'trend': trend,
            'worker_summaries': to_primitive(worker_summaries),
            **distributions,
            'top_cut_length_tasks': to_primitive(top_cut_length_tasks),
            'analysis_source_version': analysis_source_version,
        }

    def confirm_task(self, task_id: str, payload: ConfirmSnapshotTaskRequest, user: dict):
        report, records = self._records_for_date(payload.report_date)
        if report is None:
            raise WorkshopTaskNotFoundError('当日车间报表尚未生成')
        if not any((row.task_id == task_id for row in records)):
            raise WorkshopTaskNotFoundError('当日车间报表中没有找到该任务')
        now = datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec='seconds')
        confirmation = self.confirmations.confirm_task(task_id=task_id, report_id=report['report_id'], user=user, now=now, snapshot_revision=payload.snapshot_revision)
        return confirmation

    def confirm_tasks(self, payload: ConfirmTasksRequest, user: dict):
        report, records = self._records_for_date(payload.report_date)
        if report is None:
            raise WorkshopTaskNotFoundError('当日车间报表尚未生成')
        available_task_ids = {row.task_id for row in records}
        if any((task_id not in available_task_ids for task_id in payload.task_ids)):
            raise WorkshopTaskNotFoundError('当日车间报表中没有找到部分任务')
        now = datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec='seconds')
        confirmations = self.confirmations.confirm_tasks(task_ids=payload.task_ids, report_id=report['report_id'], user=user, now=now, snapshot_revisions=payload.snapshot_revisions)
        return {'items': confirmations, 'confirmed_count': len(confirmations)}

    def revoke_task_confirmation(self, task_id: str, payload: RevokeTaskRequest, user: dict):
        now = datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec='seconds')
        audit = self.confirmations.revoke_confirmation(task_id=task_id, report_id=None, user=user, reason=payload.reason, now=now)
        return audit

    def correct_task_confirmation(self, task_id: str, payload: CorrectTaskRequest, admin: dict):
        now = datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec='seconds')
        audit = self.confirmations.correct_confirmation(task_id=task_id, report_id=None, admin=admin, reason=payload.reason, now=now, new_worker_id=payload.worker_id)
        return audit

    def list_confirmation_audits(self, report_date: date | None, task_id: str | None, limit: int):
        report_id = None
        if report_date:
            report = self.reports.get_daily_report(report_date.isoformat())
            if report is None:
                return {'items': []}
            report_id = report['report_id']
        items = self.confirmations.list_confirmation_audits(task_id=task_id, report_id=report_id, limit=limit)
        return {'items': items}
