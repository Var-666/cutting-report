import pytest

from app.services.llm_service import _unwrap_repaired_analysis, _validate_citations


CONTEXT = {"facts": [{"id": "F001"}, {"id": "F002"}]}


def test_llm_evidence_citations_must_reference_existing_facts() -> None:
    _validate_citations(
        {
            "overview": "完成 10 项 [F001]。",
            "features": "环比上升 [F002]。",
            "alerts": "暂无异常 [F001]。",
        },
        CONTEXT,
    )

    with pytest.raises(ValueError, match="不存在"):
        _validate_citations(
            {
                "overview": "完成 10 项 [F999]。",
                "features": "环比上升 [F002]。",
                "alerts": "暂无异常 [F001]。",
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
            },
            CONTEXT,
        )


def test_repaired_analysis_accepts_model_specific_wrapper() -> None:
    analysis = {"overview": "完成 10 项 [F001]。"}

    assert _unwrap_repaired_analysis({"facts": [], "analysis": analysis}) is analysis
    assert _unwrap_repaired_analysis(analysis) is analysis
