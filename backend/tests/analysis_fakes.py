from app.models.domain import AnalysisResult


def model_analysis(_report_type: str, context: dict) -> AnalysisResult:
    """Return a deterministic model-shaped result for tests outside LLM transport."""
    period = context.get("report_month") or context.get("report_date") or "当前周期"
    return AnalysisResult(
        status="success",
        mode="model",
        overview=f"{period} 的生产概况已由大模型分析 [F001]。",
        features="大模型已分析当前变化情况 [F004]。",
        alerts="当前关注项需结合现场记录核对 [F006]。",
        suggestions="建议依据当前结果安排复核 [F006]。",
    )
