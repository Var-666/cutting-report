import pytest

from tests.line_fixtures import cutting_settings
from app.database.sqlserver import SqlServerClient
from app.core.report_deadline import (
    ReportOperationTimeout,
    ensure_time_remaining,
    report_deadline,
)


def test_report_deadline_expires_and_is_reset_after_scope():
    with pytest.raises(ReportOperationTimeout):
        with report_deadline(0):
            ensure_time_remaining()
    ensure_time_remaining()


def test_sql_connection_timeout_is_capped_by_remaining_report_budget(tmp_path):
    from tests.line_fixtures import source_client
    settings = cutting_settings(project_root=tmp_path, data_source_mode='sqlserver')
    client = source_client(settings)
    with report_deadline(0.01):
        connection_string = client._connection_string()
    assert "Connection Timeout=1" in connection_string


def test_report_operation_timeout_configuration_is_bounded(tmp_path):
    with pytest.raises(ValueError, match="REPORT_OPERATION_TIMEOUT_SECONDS"):
        cutting_settings(project_root=tmp_path, report_operation_timeout_seconds=4)
