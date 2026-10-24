from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import Settings
from app.models.domain import EquipmentRecord
from app.services.energy_api import EnergyApiClient, EnergyApiError, EnergyAuthenticationError
from app.services.energy_service import EquipmentEnergyService, interval_consumption, make_series
from app.services.equipment_service import summarize_equipment
from app.services.report_deadline import report_deadline
from tests.analysis_fakes import model_analysis


GAS = "bd3179be-c587-f011-a2e9-000c2974f33c"
ELECTRIC = (
    "2b00e015-d987-f011-a2e9-000c2974f33c",
    "98cbd135-d987-f011-a2e9-000c2974f33c",
    "93fae152-d987-f011-a2e9-000c2974f33c",
)
START = datetime(2026, 8, 18, 8)
END = START + timedelta(minutes=2)


def configured(**changes):
    cfg = Settings(data_source_mode="sqlserver", demo_mode=False,
        energy_api_enabled=True, energy_api_base_url="http://energy.test:10091",
        energy_api_username="test@example.test", energy_api_password="test &+# password",
        energy_equipment_ids=("Cutter01",), energy_gas_parameter_id=GAS,
        energy_electricity_parameter_ids=ELECTRIC, energy_gas_unit="Nm³",
        energy_electricity_unit="kWh", energy_gas_factor="1", energy_electricity_factor="1",
        energy_max_gap_seconds=300, energy_allow_interpolation=True, energy_cache_seconds=60,
        energy_sampling_change_at="")
    return replace(cfg, **changes)


def record(task_id="one", **changes):
    row = EquipmentRecord(task_id=task_id, equipment_id="Cutter01", project_code="P1",
        ship_no="S1", segment_name="D1", cut_length=Decimal("100"), work_time=Decimal("30"),
        gas_consumption=None, electricity_consumption=None, production_time=START,
        production_end_time=END)
    return replace(row, **changes)


def readings():
    return [{"MfId": key, "Mt": (START + timedelta(minutes=i)).isoformat(), "Mv": str(100 + i * rate)}
            for key, rate in zip((GAS, *ELECTRIC), (1, 2, 3, 4)) for i in range(3)]


class MeterStub:
    def __init__(self, rows=None, error=None):
        self.rows = readings() if rows is None else rows
        self.error = error
        self.calls = []

    def fetch(self, ids, start, end, *, force=False):
        self.calls.append((ids, start, end, force))
        if self.error:
            raise self.error
        return self.rows


class OpenerStub:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []

    def open(self, request, **kwargs):
        self.requests.append(request)
        self.timeouts.append(kwargs.get("timeout"))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return BytesIO(response if isinstance(response, bytes) else json.dumps(response).encode())


def test_energy_request_uses_remaining_report_deadline():
    opener = OpenerStub(login())
    client = EnergyApiClient(configured(), opener=opener)
    with report_deadline(1):
        client._request("api/test")
    assert 0 < opener.timeouts[0] <= 1


def login(token="private-test-token"):
    return {"IsSuccess": True, "Content": {"CompanyId": "company-test", "AuthId": token}}


def test_client_login_contract_encoding_and_cache(caplog):
    opener = OpenerStub(login(), readings(), readings())
    client = EnergyApiClient(configured(), opener=opener)
    first = client.fetch((GAS,), START, END)
    first.clear()
    assert client.fetch((GAS,), START, END)
    client.fetch((GAS,), START, END, force=True)
    assert len(opener.requests) == 3
    request = opener.requests[0]
    assert request.get_method() == "GET"
    assert parse_qs(urlsplit(request.full_url).query)["pwd"] == ["test &+# password"]
    post = opener.requests[1]
    assert post.get_method() == "POST"
    assert post.get_header("Authorization") == "BasicAuth private-test-token"
    assert post.get_header("Iszipen") == "0"
    assert json.loads(post.data) == {"CompanyId": "company-test", "Ids": [GAS],
        "Stime": "2026-08-18 08:00:00", "Etime": "2026-08-18 08:02:00"}
    assert all("SaveCollectValue" not in request.full_url for request in opener.requests)
    assert "private-test-token" not in caplog.text
    assert "test &+# password" not in repr(configured())


@pytest.mark.parametrize("payload", [json.dumps(readings()), {"IsSuccess": True, "Content": json.dumps(readings())}])
def test_client_unwraps_json_string(payload):
    client = EnergyApiClient(configured(), opener=OpenerStub(login(), payload))
    assert client.fetch((GAS,), START, END) == readings()


def test_auth_expiry_relogs_once():
    opener = OpenerStub(login("expired"), {"IsSuccess": False, "Content": "401"}, login("new"), readings())
    client = EnergyApiClient(configured(), opener=opener)
    assert client.fetch((GAS,), START, END)
    assert len(opener.requests) == 4
    assert opener.requests[-1].get_header("Authorization") == "BasicAuth new"


def test_repeated_auth_failure_is_bounded():
    error = {"IsSuccess": False, "Content": "401", "Remark": "password=secret"}
    opener = OpenerStub(login(), error, login(), error)
    with pytest.raises(EnergyAuthenticationError, match="认证失败") as exc:
        EnergyApiClient(configured(), opener=opener).fetch((GAS,), START, END)
    assert len(opener.requests) == 4
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("bad", [URLError("http://host/?pwd=secret"), b"not-json", {"IsSuccess": False, "Content": "500", "Remark": "secret"}, {"unexpected": []}])
def test_client_failure_sanitized_and_not_cached_as_empty(bad):
    opener = OpenerStub(login(), bad, readings())
    client = EnergyApiClient(configured(), opener=opener)
    with pytest.raises(EnergyApiError) as exc:
        client.fetch((GAS,), START, END)
    assert "secret" not in str(exc.value)
    with pytest.raises(EnergyApiError):
        client.fetch((GAS,), START, END)
    assert len(opener.requests) == 2
    assert client.fetch((GAS,), START, END, force=True)


def test_long_query_chunks_and_does_not_leak_partial_result():
    opener = OpenerStub(login(), readings(), URLError("offline"))
    client = EnergyApiClient(configured(), opener=opener)
    with pytest.raises(EnergyApiError):
        client.fetch((GAS,), START, START + timedelta(days=2))
    bodies = [json.loads(req.data) for req in opener.requests if req.data]
    assert bodies[0]["Etime"] == bodies[1]["Stime"]


def test_historical_month_fetch_uses_week_chunks():
    opener = OpenerStub(login(), readings(), readings())
    client = EnergyApiClient(configured(), opener=opener)

    client.fetch_month((GAS,), START, START + timedelta(days=8))

    bodies = [json.loads(req.data) for req in opener.requests if req.data]
    assert len(bodies) == 2
    assert bodies[0]["Etime"] == (START + timedelta(days=7)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert bodies[0]["Etime"] == bodies[1]["Stime"]


def test_exact_energy_uses_actual_end_time_and_configured_meter_sum():
    source = record()
    stub = MeterStub()
    result = EquipmentEnergyService(configured(), stub).enrich([source], force=True)
    row = result.records[0]
    assert row.gas_consumption == Decimal("2")
    assert row.electricity_consumption == Decimal("18")
    assert source.gas_consumption is None
    assert result.mode == "metered"
    assert row.gas_data_status == "measured"
    assert row.energy_details["end"] == END
    assert len(row.energy_details["electricity"]["meters"]) == 3
    assert stub.calls[0][3] is True
    summary = summarize_equipment(result.records, result.mode)
    assert summary["gas_record_count"] == 1
    assert summary["gas_missing_count"] == 0


def test_interpolation_is_labelled_and_can_be_disabled():
    source = record(production_time=START + timedelta(seconds=15), production_end_time=START + timedelta(seconds=90))
    result = EquipmentEnergyService(configured(), MeterStub()).enrich([source])
    row = result.records[0]
    assert row.gas_consumption == Decimal("1.250")
    assert row.electricity_consumption == Decimal("11.250")
    assert row.gas_data_status == "interpolated"
    assert summarize_equipment(result.records)["estimated_record_count"] == 1
    strict = EquipmentEnergyService(configured(energy_allow_interpolation=False), MeterStub()).enrich([source])
    assert strict.records[0].gas_consumption is None


def test_missing_meter_does_not_create_a_partial_electricity_total():
    rows = [row for row in readings() if row["MfId"] != ELECTRIC[2]]
    result = EquipmentEnergyService(configured(), MeterStub(rows)).enrich([record()])
    assert result.mode == "partial"
    assert result.records[0].gas_consumption == 2
    assert result.records[0].electricity_consumption is None
    summary = summarize_equipment(result.records)
    assert summary["total_electricity_consumption"] is None
    assert summary["electricity_missing_count"] == 1


@pytest.mark.parametrize(("values", "status"), [([100, 0, 102], "counter_reset"), ([100, "NaN", 102], "invalid_reading"), ([100, -1, 102], "invalid_reading"), ([100, None, 102], "invalid_reading")])
def test_invalid_cumulative_series_is_not_repaired(values, status):
    rows = [{"MfId": GAS, "Mt": START + timedelta(minutes=i), "Mv": value} for i, value in enumerate(values)]
    series = make_series(rows, (GAS,), "Asia/Shanghai")[GAS]
    result = interval_consumption(series, START, END, max_gap_seconds=300, interpolate=True)
    assert result["value"] is None
    assert result["status"] == status


def test_duplicate_conflict_gaps_missing_and_zero():
    rows = [{"MfId": GAS, "Mt": START, "Mv": 100}, {"MfId": GAS, "Mt": END, "Mv": 100}]
    def calc(data, gap=300):
        return interval_consumption(make_series(data, (GAS,), "Asia/Shanghai")[GAS], START, END, max_gap_seconds=gap, interpolate=True)
    assert calc(rows)["value"] == 0
    assert calc(rows + [rows[0]])["value"] == 0
    assert calc(rows, 60)["status"] == "gap"
    assert calc(rows[:1])["value"] is None
    assert calc(rows + [{**rows[0], "Mv": 99}])["status"] == "invalid_reading"


@pytest.mark.parametrize("left,right,status", [
    (-900, 0, "measured"),  # historical 15-minute readings still valid
    (0, 65, "measured"),  # one-minute sampling with timestamp jitter
    (0, 180, "measured"),
    (0, 181, "gap"),
    (0, 1200, "gap"),
    (-900, 60, "measured"),  # final historical sample + first new sample
    (-900, 181, "gap"),
    (-1801, 60, "gap"),
])
def test_sampling_transition_validates_each_side(left, right, status):
    change = datetime(2026, 9, 4)
    start, end = (change + timedelta(seconds=value) for value in (left, right))
    points = ([(start, Decimal(100)), (end, Decimal(110))], False)
    result = interval_consumption(points, start, end, max_gap_seconds=1800,
        interpolate=True, sampling_change_at=change, new_max_gap_seconds=180)
    assert result["status"] == status
    assert result["value"] == (Decimal(10) if status == "measured" else None)


def test_legacy_tasks_use_new_policy_and_keep_subminute_interpolation():
    start = datetime(2026, 9, 4, 8)
    rows = [{"MfId": key, "Mt": start + timedelta(minutes=i), "Mv": 100 + i}
            for key in (GAS, *ELECTRIC) for i in range(5)]
    # Explicit UTC time must normalize to midnight in Asia/Shanghai.
    cfg = configured(energy_sampling_change_at="2026-09-03T16:00:00Z")
    task = record(production_time=start + timedelta(seconds=30),
                  production_end_time=start + timedelta(minutes=1, seconds=30))
    result = EquipmentEnergyService(cfg, MeterStub(rows)).enrich([task])
    assert result.records[0].gas_consumption == Decimal(1)
    assert result.records[0].gas_data_status == "interpolated"
    sparse = [row for row in rows if row["Mt"] in {start, start + timedelta(minutes=4)}]
    result = EquipmentEnergyService(cfg, MeterStub(sparse)).enrich([task])
    assert result.records[0].gas_consumption is None
    assert result.records[0].gas_data_status == "gap"


def test_units_and_device_binding_are_required():
    stub = MeterStub()
    result = EquipmentEnergyService(configured(energy_equipment_ids=()), stub).enrich([record()])
    assert result.records[0].gas_consumption is None
    assert not stub.calls
    result = EquipmentEnergyService(configured(energy_gas_unit=""), stub).enrich([record()])
    assert result.records[0].gas_consumption is None
    assert result.records[0].electricity_consumption == 18
    assert GAS not in stub.calls[-1][0]
    result = EquipmentEnergyService(configured(), MeterStub()).enrich([record(), record("other", equipment_id="Cutter02")])
    unmapped = next(row for row in result.records if row.task_id == "other")
    assert unmapped.gas_data_status == "unmapped"
    assert unmapped.gas_consumption is None


def test_overlap_including_filtered_context_is_not_double_counted():
    first = record()
    other = record("other", production_time=START + timedelta(seconds=10))
    stub = MeterStub()
    result = EquipmentEnergyService(configured(), stub).enrich([first], context_records=[first, other])
    assert result.records[0].gas_data_status == "overlap"
    assert not stub.calls


@pytest.mark.parametrize("end", [None, START, START - timedelta(seconds=1)])
def test_no_start_plus_cutting_duration_fallback(end):
    result = EquipmentEnergyService(configured(), MeterStub()).enrich([record(production_end_time=end)])
    assert result.records[0].gas_data_status == "invalid_interval"


def test_cross_midnight_and_timezone_normalization():
    start = datetime(2026, 8, 18, 23, 59)
    end = start + timedelta(minutes=2)
    rows = [{"MfId": key, "Mt": (start + timedelta(minutes=i) - timedelta(hours=8)).isoformat() + "Z", "Mv": 100 + i}
            for key in (GAS, *ELECTRIC) for i in range(3)]
    result = EquipmentEnergyService(configured(), MeterStub(rows)).enrich([record(production_time=start, production_end_time=end)])
    assert result.records[0].gas_consumption == 2
    assert result.records[0].electricity_consumption == 6


def test_failure_and_disabled_api_never_fall_back_to_mock():
    source = record(gas_consumption=Decimal("999"), electricity_consumption=Decimal("999"))
    for cfg, stub in [(configured(), MeterStub(error=EnergyApiError("内网不可达"))), (configured(energy_api_enabled=False), MeterStub())]:
        result = EquipmentEnergyService(cfg, stub).enrich([source])
        assert result.mode == "unavailable"
        assert result.records[0].gas_consumption is None
        assert result.records[0].electricity_consumption is None
        assert summarize_equipment(result.records)["total_gas_consumption"] is None


def test_demo_is_explicit_and_never_reads_real_meters():
    stub = MeterStub()
    result = EquipmentEnergyService(configured(data_source_mode="demo"), stub).enrich([record()])
    assert result.mode == "mock"
    assert result.records[0].gas_data_status == "mock"
    assert not stub.calls


@pytest.mark.parametrize("changes", [{"energy_electricity_parameter_ids": (ELECTRIC[0], ELECTRIC[0])}, {"energy_gas_factor": "NaN"}, {"energy_electricity_factor": "0"}, {"energy_max_gap_seconds": 0}, {"energy_gas_parameter_id": "bad"}])
def test_invalid_config_fails_closed(changes):
    stub = MeterStub()
    result = EquipmentEnergyService(configured(**changes), stub).enrich([record()])
    assert result.mode == "unavailable"
    assert not stub.calls


def test_unified_device_page_export_and_analysis_share_meter_values(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.api.analysis import AnalysisRequest, analyze_report
    from app.api.energy import get_task_energy_day
    from app.database.metadata import MetadataRepository
    from app.services.pipeline import ReportPipeline
    from app.services.query_service import EquipmentQueryResult
    from app.services.report_analysis_service import (
        analysis_table_source_version,
        combined_analysis_source_version,
    )

    cfg = configured(project_root=tmp_path, llm_enabled=True)
    cfg.ensure_runtime_dirs()
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "echarts.min.js").write_text("window.echarts={};", encoding="utf-8")
    metadata = MetadataRepository(cfg.metadata_db_path, device_id='Cutter01')
    metadata.initialize()
    pipeline = ReportPipeline(cfg, metadata)
    pipeline.analysis_service.llm.analyze_context = model_analysis
    pipeline.energy_period_service.client = MeterStub()
    rows = [record()]
    monkeypatch.setattr(pipeline.query_service, "fetch_equipment_data", lambda filters, force=False: EquipmentQueryResult(rows, END))
    monkeypatch.setattr(
        pipeline.query_service,
        "fetch_equipment_month_metrics",
        lambda _month: {
            START.date().isoformat(): {
                "task_count": 1,
                "work_time_seconds": Decimal("30"),
            }
        },
    )
    monkeypatch.setattr(
        pipeline.query_service,
        "fetch_equipment_range_metrics",
        lambda _start, _end: {
            START.date().isoformat(): {
                "task_count": 1,
                "work_time_seconds": Decimal("30"),
            }
        },
    )
    request = SimpleNamespace(state=SimpleNamespace(request_id="test"), app=SimpleNamespace(state=SimpleNamespace(
        query_service=pipeline.query_service,
        energy_period_service=pipeline.energy_period_service,
        report_analysis_service=pipeline.analysis_service,
    )))
    request.query_params = {'device_id': 'Cutter01'}
    request.app.state.devices = {'Cutter01': request.app.state}
    page = get_task_energy_day(request=request, report_date=START.date(), refresh=True)["data"]
    assert page["summary"]["energy_data_mode"] == "metered"
    assert page["summary"]["total_gas_consumption"] == 2
    assert page["summary"]["total_electricity_consumption"] == 18
    assert page["units"] == {"gas": "Nm³", "electricity": "kWh"}
    summary_rows = pipeline.energy_period_service.get_monthly_range(
        date(2025, 9, 1), START.date()
    )["rows"]
    visible_version = combined_analysis_source_version(
        page["analysis_source_version"],
        analysis_table_source_version(summary_rows),
    )
    analysis_response = analyze_report(
        AnalysisRequest(
            report_type="equipment",
            report_date=START.date(),
            data_version=visible_version,
        ),
        request,
    )
    assert analysis_response["data"]["data_version"] == visible_version
    page_analysis_facts = {
        fact["id"]: fact["value"] for fact in analysis_response["data"]["facts"]
    }
    assert Decimal(page_analysis_facts["F003"].split()[0]) == 2
    assert Decimal(page_analysis_facts["F004"].split()[0]) == 18
    assert page_analysis_facts["F006"] == "0 项"
    assert len(page_analysis_facts) == 6
    rendered = []
    original_render = pipeline.report_service.render

    def render(**kwargs):
        rendered.append(kwargs["report"])
        return original_render(**kwargs)

    monkeypatch.setattr(pipeline.report_service, "render", render)
    now = datetime.now().isoformat()
    metadata.create_job(report_id="energy_test", report_date=START.date().isoformat(),
        project_code=None, ship_no=None, request_id="test", created_at=now, report_type="equipment")
    pipeline.run(metadata.claim_next_job(now))
    report = rendered[0]
    assert report["equipment_summary"] == page["summary"]
    assert report["equipment_records"] == page["records"]
    assert report["charts"]["equipment_detail"]["series"][0]["data"] == [18]
    assert report["charts"]["equipment_detail"]["series"][1]["data"] == [2]
    job = metadata.get_job("energy_test")
    html = (cfg.reports_dir / job["relative_path"]).read_text(encoding="utf-8")
    assert "稳定模拟数据" not in html
    assert "已匹配 1/1 项" in html
    assert "月度每日能耗汇总" in html
    assert "<th>任务数</th><th>切割时长</th>" in html
    assert "<th>汇总</th><th>1</th><th>00:00:30</th>" in html
    assert "当日任务汇总" not in html
    assert "当月汇总</th>" not in html
    assert "≈" not in html
    analysis = pipeline.analysis_service.analyze_equipment(START.date().isoformat(),
        [replace(rows[0], gas_consumption=Decimal("2"), electricity_consumption=Decimal("18"))],
        energy_data_mode="metered", energy_units=page["units"])
    facts = {fact["id"]: fact["value"] for fact in analysis.facts}
    assert Decimal(facts["F003"].split()[0]) == 2
    assert "Nm³" in facts["F003"]
    assert Decimal(facts["F004"].split()[0]) == 18
    assert "kWh" in facts["F004"]
    assert facts["F003"] == "2.0 Nm³"
    assert facts["F004"] == "18.0 kWh"
    assert "（" not in facts["F003"] + facts["F004"]
    assert analysis.mode == "model"


def test_same_day_range_reuses_task_meter_scope_and_interpolation(tmp_path, monkeypatch):
    from app.database.metadata import MetadataRepository
    from app.services.pipeline import ReportPipeline
    from app.services.query_service import EquipmentQueryResult
    from app.services.report_analysis_service import equipment_analysis_source_version

    cfg = replace(configured(
        energy_equipment_ids=(),
        energy_report_equipment_ids=("Cutter01",),
    ), project_root=tmp_path)
    cfg.ensure_runtime_dirs()
    metadata = MetadataRepository(cfg.metadata_db_path, device_id='Cutter01')
    metadata.initialize()
    pipeline = ReportPipeline(cfg, metadata)
    meter = MeterStub()
    pipeline.energy_period_service.client = meter
    selected = record(
        production_time=START + timedelta(seconds=30),
        production_end_time=START + timedelta(minutes=1, seconds=30),
    )
    rows = [selected, record("other", equipment_id="Cutter02")]
    monkeypatch.setattr(
        pipeline.query_service, "fetch_equipment_data",
        lambda filters, force=False: EquipmentQueryResult(rows, END),
    )

    daily = pipeline.energy_period_service.get_task_day(START.date(), rows)
    selected_range = pipeline.range_service.equipment(START.date(), START.date())
    task_report = selected_range["task_report"]
    period_report = selected_range["period_report"]

    assert task_report["records"] == daily["records"]
    assert task_report["summary"] == daily["summary"]
    assert equipment_analysis_source_version(task_report) == equipment_analysis_source_version(daily)
    assert task_report["equipment_scope"] == daily["equipment_scope"] == ["Cutter01"]
    assert task_report["source_task_count"] == 2
    assert task_report["excluded_task_count"] == 1
    assert task_report["records"][0]["gas_data_status"] == "interpolated"
    assert task_report["records"][0]["electricity_data_status"] == "interpolated"
    assert period_report["summary"]["gas"]["status"] == "interpolated"
    assert period_report["summary"]["electricity"]["estimated_count"] == 1
    assert [call[0] for call in meter.calls] == [(GAS, *ELECTRIC)] * 2


def test_analysis_does_not_report_missing_energy_as_zero(tmp_path):
    from app.database.metadata import MetadataRepository
    from app.services.report_analysis_service import ReportAnalysisService
    cfg = configured(project_root=tmp_path, llm_enabled=True)
    cfg.ensure_runtime_dirs()
    metadata = MetadataRepository(cfg.metadata_db_path, device_id='Cutter01')
    metadata.initialize()
    service = ReportAnalysisService(cfg, metadata)
    service.llm.analyze_context = model_analysis
    analysis = service.analyze_equipment(START.date().isoformat(), [record()], energy_data_mode="unavailable")
    facts = {fact["id"]: fact["value"] for fact in analysis.facts}
    assert facts["F003"] == "缺失"
    assert facts["F004"] == "缺失"
    assert facts["F006"] == "1 项"
    assert len(facts) == 6
