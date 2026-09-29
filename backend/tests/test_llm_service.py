import json

import pytest

from app.config import Settings
from app.services.llm_service import (
    AnalysisOutputValidationError,
    LlmService,
    analysis_system_prompt,
)


def _model_response(monkeypatch, raw_content):
    requests = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": raw_content}}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, **kwargs):
            requests.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr("app.services.llm_service.httpx.Client", FakeClient)
    return requests


def _service(tmp_path):
    return LlmService(Settings(
        project_root=tmp_path,
        llm_enabled=True,
        llm_api_key="test-key",
        llm_base_url="http://llm.test",
    ))


def test_model_analysis_uses_first_result_without_content_guardrails_or_repair(tmp_path, monkeypatch):
    content = {
        "overview": "完成 999 项任务，没有事实引用。" + "分析说明。" * 100,
        "features": "任务数显著增长，原因是订单增加。",
        "alerts": "设备故障已经发生。",
        "suggestions": "建议继续观察。",
    }
    requests = _model_response(monkeypatch, json.dumps(content))

    result = _service(tmp_path).analyze_context("leadership", {"facts": []})

    assert result.mode == "model"
    assert {key: getattr(result, key) for key in content} == content
    assert len(requests) == 1


def test_model_receives_facts_report_tables_and_scope(tmp_path, monkeypatch):
    content = {key: key for key in ("overview", "features", "alerts", "suggestions")}
    requests = _model_response(monkeypatch, json.dumps(content))
    context = {
        "report_month": "2026-08",
        "facts": [{"id": "F001", "value": "10 项"}],
        "tables": [
            {
                "name": "区间每日明细",
                "row_count": 1,
                "rows": [{"period_start": "2026-08-01", "task_count": 10}],
            }
        ],
        "raw_records": [{"unvalidated_metric": 999}],
    }

    _service(tmp_path).analyze_context("leadership", context)

    submitted = json.loads(requests[0]["messages"][1]["content"])
    assert set(submitted) == {
        "report_type",
        "report_date",
        "report_month",
        "facts",
        "tables",
    }
    assert submitted["facts"] == context["facts"]
    assert submitted["tables"] == context["tables"]
    assert "raw_records" not in submitted


@pytest.mark.parametrize(
    ("raw_content", "error_fragment"),
    [
        ("not json", "JSON"),
        (json.dumps({"overview": "已有分析"}), "四段内容"),
        (json.dumps({"overview": "", "features": "a", "alerts": "b", "suggestions": "c"}), "四段内容"),
    ],
)
def test_only_unrenderable_model_results_are_rejected(
    tmp_path, monkeypatch, raw_content, error_fragment
):
    requests = _model_response(monkeypatch, raw_content)

    with pytest.raises(AnalysisOutputValidationError, match=error_fragment):
        _service(tmp_path).analyze_context("leadership", {"facts": []})

    assert len(requests) == 1


def test_report_prompts_match_the_four_section_ui_and_available_facts():
    prompts = {
        report_type: analysis_system_prompt(report_type)
        for report_type in ("leadership", "workshop", "equipment")
    }
    for prompt in prompts.values():
        assert all(field in prompt for field in ("overview", "features", "alerts", "suggestions"))
        assert "没有对比数据就不写升降" in prompt
        assert "只返回 JSON 对象" in prompt
        assert "每段通常写两句，必要时三句" in prompt
        assert "先核对什么、核对后如何跟进" in prompt
        assert "不要只写一句指标复述" in prompt
        assert "两张表的完整查询结果" in prompt
        assert "不得自行重算并推翻 facts" in prompt

    assert "记录天数不足" in prompts["leadership"]
    assert "不要把缺失当成工程集中" in prompts["leadership"]
    assert "订单、交付" in prompts["leadership"]
    assert "待工人确认" in prompts["workshop"]
    assert "人员效率低" in prompts["workshop"]
    assert "不自行给数据来源分类" in prompts["equipment"]
    assert "不等于模拟数据" in prompts["equipment"]
    assert "不要笼统说已记录数值只能作为参考" in prompts["equipment"]
    assert "合计未覆盖全部任务" in prompts["equipment"]
    assert "实测、模拟还是不可用" not in prompts["equipment"]
