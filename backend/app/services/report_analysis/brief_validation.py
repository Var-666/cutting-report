"""Bind model selections to server facts and scoped business references."""
from __future__ import annotations

import json
import re
from decimal import Decimal

from app.models.analysis_brief import AnalysisBrief, BriefInput
from app.services.report_analysis.brief import model_context


def _numbers(text: str) -> set[Decimal]:
    return {Decimal(number.replace(',', '')) for number in re.findall(r'(?<![A-Za-z])\d[\d,]*(?:\.\d+)?', text)}


def validate_brief(content: object, context: dict) -> dict:
    draft = BriefInput.model_validate(content)
    facts = {fact['id']: fact for fact in context['facts']}
    candidates = {candidate['id']: candidate for candidate in context['candidates']}
    if len(set(draft.key_metric_ids)) != len(draft.key_metric_ids) or any(identifier not in facts for identifier in draft.key_metric_ids):
        raise ValueError('关键指标引用不存在或重复')
    selected = set()
    findings = []
    for finding in draft.findings:
        candidate = candidates.get(finding.candidate_id)
        if candidate is None or finding.candidate_id in selected:
            raise ValueError('分析候选引用不存在或重复')
        if (not finding.evidence_ids or not set(finding.evidence_ids) <= set(candidate['evidence_ids'])
            or not set(finding.evidence_ids) <= facts.keys()):
            raise ValueError('发现包含无效事实引用')
        selected.add(finding.candidate_id)
        references = context['_references'][finding.candidate_id]
        scope = context['scope']
        if any(ref.get('device_id', '') != scope.get('device_id', '')
               or (ref.get('production_date') and not scope['period_start'] <= ref['production_date'] <= scope['period_end'])
               for ref in references):
            raise ValueError('业务关联不属于当前产线或周期')
        findings.append(dict(**finding.model_dump(), references=references, reference_count=len(references)))
    for action in draft.actions:
        if action.candidate_id not in selected:
            raise ValueError('建议必须关联真实发现')
        if any(term in action.detail for term in ('加强生产管理', '持续关注相关指标', '进一步提高工作效率')):
            raise ValueError('建议缺少具体跟进对象和动作')
    authoritative = json.dumps(model_context(context), ensure_ascii=False)
    # shortcut: token checks do not establish causality; add targeted checks for concrete false claims.
    allowed_numbers = _numbers(authoritative)
    direction_pattern = r'(上升|增长|增加|下降|减少|降低)\s*(\d+(?:\.\d+)?)%'
    def direction_key(direction, amount):
        return (1 if direction in {'上升', '增长', '增加'} else -1, Decimal(amount))
    allowed_changes = {direction_key(direction, amount) for direction, amount in re.findall(direction_pattern, authoritative)}
    allowed_dates = set(re.findall(r'\d{4}-\d{2}-\d{2}', authoritative))
    prose = [draft.headline, draft.summary]
    prose += [text for finding in draft.findings for text in (finding.title, finding.detail)]
    prose += [text for action in draft.actions for text in (action.title, action.detail)]
    for text in prose:
        if not text.strip():
            raise ValueError('分析结论或正文为空')
        if re.search(r'\b[FC]\d{3}\b|\bC_[a-z_]+\b', text):
            raise ValueError('正文不应展示内部引用编号')
        if any(direction_key(direction, amount) not in allowed_changes
               for direction, amount in re.findall(direction_pattern, text)):
            raise ValueError('分析变化方向或百分比与事实不符')
        if not _numbers(text) <= allowed_numbers:
            raise ValueError('分析包含输入中不存在的数量')
        if not set(re.findall(r'\d{4}-\d{2}-\d{2}', text)) <= allowed_dates:
            raise ValueError('分析包含输入中不存在的日期')
    return AnalysisBrief(**draft.model_dump(exclude={'findings'}), findings=findings,
                         scope=context['scope'], data_quality=context['data_quality']).model_dump()
