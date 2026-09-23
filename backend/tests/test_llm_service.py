import json

import pytest

from app.config import Settings

from app.services.llm_service import (
    LlmService,
    _unwrap_repaired_analysis,
    _validate_citation_relevance,
    _validate_citations,
    _validate_grounded_numbers,
    _validate_scenario_boundaries,
    analysis_system_prompt,
)


CONTEXT = {
    "facts": [
        {"id": "F001", "value": "10 项", "source": "上一周期 8 项"},
        {"id": "F002", "value": "上升 25.0%", "source": "10 项对比 8 项"},
    ]
}


def test_llm_evidence_citations_must_reference_existing_facts() -> None:
    _validate_citations(
        {
            "overview": "完成 10 项 [F001]。",
            "features": "环比上升 [F002]。",
            "alerts": "暂无异常 [F001]。",
            "suggestions": "建议核对任务记录 [F001]。",
        },
        CONTEXT,
    )

    with pytest.raises(ValueError, match="不存在"):
        _validate_citations(
            {
                "overview": "完成 10 项 [F999]。",
                "features": "环比上升 [F002]。",
                "alerts": "暂无异常 [F001]。",
                "suggestions": "建议核对任务记录 [F001]。",
            },
            CONTEXT,
        )


def test_llm_evidence_citations_are_required_for_data_sections() -> None:
    with pytest.raises(ValueError, match="features"):
        _validate_citations(
            {
                "overview": "完成 10 项 [F001]。",
                "features": "环比上升。",
                "alerts": "暂无异常 [F001]。",
                "suggestions": "建议核对任务记录 [F001]。",
            },
            CONTEXT,
        )


def test_llm_evidence_citations_are_required_for_every_sentence_and_at_the_end() -> None:
    with pytest.raises(ValueError, match="features"):
        _validate_citations(
            {
                "overview": "完成 10 项 [F001]。",
                "features": "环比上升 [F002]。第二句没有依据。",
                "alerts": "暂无异常 [F001]。",
                "suggestions": "建议核对任务记录 [F001]。",
            },
            CONTEXT,
        )

    with pytest.raises(ValueError, match="句末"):
        _validate_citations(
            {
                "overview": "[F001] 完成 10 项。",
                "features": "环比上升 [F002]。",
                "alerts": "暂无异常 [F001]。",
                "suggestions": "建议核对任务记录 [F001]。",
            },
            CONTEXT,
        )


def test_llm_numeric_claims_must_be_supported_by_same_sentence_facts() -> None:
    valid = {
        "overview": "本期完成 10 项，上期完成 8 项 [F001]。",
        "features": "任务数上升 25.0% [F002]。",
        "alerts": "当前 10 项任务需要结合现场判断 [F001]。",
        "suggestions": "建议核对这 10 项任务 [F001]。",
    }
    _validate_grounded_numbers(valid, CONTEXT)

    invalid = {**valid, "overview": "本期完成 12 项 [F001]。"}
    with pytest.raises(ValueError, match="12"):
        _validate_grounded_numbers(invalid, CONTEXT)

    wrong_unit = {**valid, "overview": "本期任务上升 10% [F001]。"}
    with pytest.raises(ValueError, match="10%"):
        _validate_grounded_numbers(wrong_unit, CONTEXT)


def test_dates_cannot_supply_metric_numbers_or_unrelated_dates() -> None:
    context = {
        "report_date": "2026-08-18",
        "facts": [{"id": "F001", "value": "10 项", "source": "2026-08-18 共 10 项任务"}],
    }
    content = {
        "overview": "2026-08-18 共 10 项任务 [F001]。",
        "features": "当前有 10 项任务 [F001]。",
        "alerts": "任务需核对 [F001]。",
        "suggestions": "建议复核任务记录 [F001]。",
    }
    _validate_grounded_numbers(content, context)

    with pytest.raises(ValueError, match="8"):
        _validate_grounded_numbers({**content, "features": "当前有 8 项任务 [F001]。"}, context)
    with pytest.raises(ValueError, match="日期"):
        _validate_grounded_numbers({**content, "overview": "2026-08-19 共 10 项任务 [F001]。"}, context)


def test_claimed_measurement_unit_and_direction_must_match_evidence() -> None:
    context = {
        "facts": [
            {"id": "F001", "value": "10 项", "source": "当前 10 项任务"},
            {"id": "F002", "value": "上升 25.0%", "source": "任务数环比上升 25.0%"},
        ]
    }
    content = {
        "overview": "当前 10 项任务 [F001]。",
        "features": "任务数环比上升 25.0% [F002]。",
        "alerts": "任务趋势需核对 [F002]。",
        "suggestions": "建议复核任务记录 [F001]。",
    }
    _validate_grounded_numbers(content, context)

    with pytest.raises(ValueError, match="单位"):
        _validate_grounded_numbers({**content, "overview": "当前切割长度 10 米 [F001]。"}, context)
    with pytest.raises(ValueError, match="升降方向"):
        _validate_grounded_numbers({**content, "features": "任务数环比下降 25.0% [F002]。"}, context)
    with pytest.raises(ValueError, match="升降方向"):
        _validate_grounded_numbers({**content, "features": "任务数环比下降约 25.0% [F002]。"}, context)


def test_model_receives_only_citable_facts_and_report_scope(tmp_path, monkeypatch) -> None:
    submitted = {}
    content = {
        "overview": "任务数上升 25.0% [F002]。",
        "features": "主要工程任务需跟踪 [F003]。",
        "alerts": "高位生产日需核对 [F003]。",
        "suggestions": "建议复核任务安排 [F001]。",
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(content)}}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, **kwargs):
            submitted.update(json.loads(kwargs["json"]["messages"][1]["content"]))
            return FakeResponse()

    monkeypatch.setattr("app.services.llm_service.httpx.Client", FakeClient)
    settings = Settings(
        project_root=tmp_path,
        llm_enabled=True,
        llm_api_key="test-key",
        llm_base_url="http://llm.test",
    )
    context = {
        "report_month": "2026-08",
        "facts": [
            {"id": "F001", "label": "任务数", "value": "10 项", "source": "当前共 10 项任务"},
            {"id": "F002", "label": "任务数环比", "value": "上升 25.0%", "source": "任务数环比上升 25.0%"},
            {"id": "F003", "label": "主要工程与高位日", "value": "主要工程 A，高位生产日需关注", "source": "工程 A；高位生产日需核对"},
        ],
        "raw_records": [{"unvalidated_metric": 999}],
    }

    result = LlmService(settings).analyze_context("leadership", context)

    assert result.mode == "model"
    assert set(submitted) == {"report_type", "report_date", "report_month", "facts"}
    assert "raw_records" not in submitted


def test_citations_must_point_to_a_fact_relevant_to_the_claim() -> None:
    context = {
        "facts": [
            {"id": "F001", "label": "任务总数", "source": "共 10 项任务"},
            {"id": "F002", "label": "工人确认率", "source": "确认率 80.0%"},
            {"id": "F003", "label": "已匹配任务气耗", "source": "气耗 20.0 Nm³"},
            {"id": "F004", "label": "已匹配任务电耗", "source": "电耗 30.0 kWh"},
        ]
    }
    valid = {
        "overview": "本期共有 10 项任务 [F001]。",
        "features": "工人确认率为 80.0% [F002]。",
        "alerts": "气耗与电耗数据需要核对 [F003][F004]。",
        "suggestions": "建议复核待确认任务 [F002]。",
    }
    _validate_citation_relevance(valid, context)

    wrong = {**valid, "features": "工人确认率为 80.0% [F001]。"}
    with pytest.raises(ValueError, match="不相关"):
        _validate_citation_relevance(wrong, context)

    incomplete = {**valid, "alerts": "气耗与电耗数据需要核对 [F003]。"}
    with pytest.raises(ValueError, match="电耗"):
        _validate_citation_relevance(incomplete, context)


def test_report_scenarios_add_role_specific_analysis_boundaries() -> None:
    assert "订单、交付、产能" in analysis_system_prompt("leadership")
    assert "人员效率" in analysis_system_prompt("workshop")
    assert "能耗缺失不等于零" in analysis_system_prompt("equipment")


def test_scenario_boundaries_reject_definite_causes_and_unsupported_business_claims() -> None:
    workshop = {
        "overview": "车间任务趋势需要关注 [F001]。",
        "features": "工时记录需要结合任务结构判断 [F002]。",
        "alerts": "等待造成工时异常 [F002]。",
        "suggestions": "建议复核任务记录 [F001]。",
    }
    with pytest.raises(ValueError, match="未证实原因"):
        _validate_scenario_boundaries("workshop", workshop)

    equipment = {
        "overview": "设备任务趋势需要关注 [F001]。",
        "features": "当前数据说明设备故障 [F002]。",
        "alerts": "能耗数据需要核对 [F002]。",
        "suggestions": "建议检查计量记录 [F002]。",
    }
    with pytest.raises(ValueError, match="业务结论"):
        _validate_scenario_boundaries("equipment", equipment)


def test_scenario_boundaries_allow_explicit_limits_and_require_specific_actions() -> None:
    content = {
        "overview": "设备任务趋势需要关注 [F001]。",
        "features": "当前数据无法判断设备故障或设备效率 [F002]。",
        "alerts": "能耗覆盖情况需要核对 [F002]。",
        "suggestions": "建议检查计量记录 [F002]。",
    }
    _validate_scenario_boundaries("equipment", content)

    content["suggestions"] = "建议持续提升管理水平 [F002]。"
    with pytest.raises(ValueError, match="可执行动作"):
        _validate_scenario_boundaries("equipment", content)

    content["suggestions"] = "建议排查设备故障并更换部件 [F002]。"
    with pytest.raises(ValueError, match="业务结论"):
        _validate_scenario_boundaries("equipment", content)


def test_scenario_boundaries_reject_subjective_degrees_and_leadership_inference() -> None:
    content = {
        "overview": "任务数显著上升 [F001]。",
        "features": "工程结构需要跟踪 [F002]。",
        "alerts": "高位生产日需要核对 [F002]。",
        "suggestions": "建议复核排产记录 [F002]。",
    }
    with pytest.raises(ValueError, match="程度描述"):
        _validate_scenario_boundaries("leadership", content)

    content["overview"] = "任务数呈上升趋势 [F001]。"
    content["features"] = "任务上升说明订单增加 [F002]。"
    with pytest.raises(ValueError, match="业务结论"):
        _validate_scenario_boundaries("leadership", content)


def test_repaired_analysis_accepts_model_specific_wrapper() -> None:
    analysis = {"overview": "完成 10 项 [F001]。"}

    assert _unwrap_repaired_analysis({"facts": [], "analysis": analysis}) is analysis
    assert _unwrap_repaired_analysis(analysis) is analysis


def test_removed_fact_cannot_be_referenced_in_advice():
    section = "suggestions"
    content = {
        "overview": "任务 [F001]。",
        "features": "时长 [F002]。",
        "alerts": "任务 [F001]。",
        section: "依据旧编号 [F013]。",
    }
    with pytest.raises(ValueError, match="不存在"):
        _validate_citations(content, CONTEXT)
