"""HTTP adaptation for shared statistics reads; business providers own the payload."""
from fastapi import HTTPException, Request

from app.api.devices import device_state
from app.database.sqlserver import SqlServerConfigurationError
from app.modules.reports.snapshots import SnapshotRefreshBusy, SnapshotRefreshRejected, SnapshotRefreshFailed


def statistics_page(request: Request, report_type: str, query: dict, *, refresh: bool = False) -> dict | None:
    provider = device_state(request).report_provider
    if provider.snapshot_service is None:
        return None
    try:
        return provider.page(report_type, query, force=refresh)
    except SnapshotRefreshBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={
            'Retry-After': '5', 'X-Report-Error': 'snapshot_refresh_busy'}) from None
    except SnapshotRefreshRejected as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={
            'X-Report-Error': 'snapshot_queue_full'}) from None
    except SnapshotRefreshFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={'X-Report-Error': 'source_unavailable'}) from None
    except SqlServerConfigurationError:
        raise HTTPException(status_code=503, detail='统计数据源暂不可用') from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
