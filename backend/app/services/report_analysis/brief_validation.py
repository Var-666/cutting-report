"""Bind model selections to server facts and scoped business references."""
from __future__ import annotations

import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pydantic import ValidationError

from app.models.analysis_brief import AnalysisBrief, BriefInput
from app.services.report_analysis.brief import model_context


def _numbers(text: str) -> set[Decimal]:
    return {Decimal(number.replace(',', '')) for number in re.findall(r'(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?', text)}


def _matches_precision(number: str, values: set[Decimal]) -> bool:
    amount = Decimal(number.replace(',', ''))
    precision = Decimal(1).scaleb(amount.as_tuple().exponent)
    return any(value == amount or (value.as_tuple().exponent <= amount.as_tuple().exponent
               and value.quantize(precision, rounding=ROUND_HALF_UP) == amount) for value in values)


_WELDING_SCOPE = r'(?:(?P<scope>本期|本次查询|所选期间|全线|汇总|\d{4}-\d{2}-\d{2})[\s,，:：]*)?'
_WELDING_CLAIMS = {
    'job_count': re.compile(_WELDING_SCOPE + r'(?:焊接)?作业(?:总数|数量|数|量)?[\s：:为约共计达]*(?P<amount>-?[0-9][0-9,]{0,19})\s*(?:条|项|个)'),
    'weld_length_m': re.compile(_WELDING_SCOPE + r'焊(?:接)?(?:总)?长度[\s：:为约共计达]*(?P<amount>-?[0-9][0-9,]{0,19}(?:\.\d{1,8})?)\s*(?:米|m\b)'),
    'working_hours': re.compile(_WELDING_SCOPE + r'(?:作业用时|作业工时|工作时长|工作用时)[\s：:为约共计达]*(?P<amount>-?[0-9][0-9,]{0,19}(?:\.\d{1,8})?)\s*(?:小时|h\b)'),
}


def validate_welding_quantities(content: dict, context: dict) -> None:
    """Check explicit metrics against values from the supplied report's comparable tables."""
    summary = {item['label']: item['value'] for item in context['facts']}
    rows = [summary]
    for table in context['tables']:
        if isinstance(table['rows'], list):
            rows.extend(table['rows'])
    text = '\n'.join(content[key] for key in ('overview', 'features', 'alerts', 'suggestions'))
    for metric, pattern in _WELDING_CLAIMS.items():
        for claim in pattern.finditer(text):
            scope = claim['scope']
            if scope is None:
                evidence = rows
            elif scope[0].isdigit():
                evidence = [row for row in rows if row.get('production_date') == scope]
            else:
                evidence = [summary]
            values = {Decimal(str(value)) for row in evidence if type(value := row.get(metric)) in (int, float)}
            if not _matches_precision(claim['amount'], values):
                raise ValueError('焊接分析中的数量与报表事实不符')
    quality_terms = {'invalid_length_rows': ('长度',), 'invalid_duration_rows': ('用时', '时长', '工时'),
                     'invalid_serial_rows': ('流水号',), 'invalid_time_rows': ('时间', '日期'),
                     'invalid_part_rows': ('工件',), 'unverified_state_rows': ('状态',)}
    for table in context['tables']:
        for row in table['rows'] if isinstance(table['rows'], list) else []:
            for key, terms in quality_terms.items():
                if row.get('quality', {}).get(key, 0) and not any(term in content['alerts'] for term in terms):
                    raise ValueError(f'焊接异常分析遗漏{terms[0]}问题，请在alerts说明已知异常及待核查方向')


_QUANTITY_PATTERN = re.compile(r'(?<![A-Za-z0-9_.])(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>%|米|Nm³|kWh)')


def _check_rounded_quantities(text: str, authoritative: str, change_percentages: set[Decimal], lengths: set[Decimal]) -> str:
    values: dict[str, set[Decimal]] = {'%': {abs(value) for value in change_percentages}, '米': set(lengths)}
    for match in _QUANTITY_PATTERN.finditer(authoritative):
        values.setdefault(match['unit'], set()).add(Decimal(match['number'].replace(',', '')))

    def checked(match: re.Match) -> str:
        if not _matches_precision(match['number'], values.get(match['unit'], set())):
            raise ValueError(f'分析数量或单位与输入不符：生成={match["number"][:32]}{match["unit"]}')
        return ''

    return _QUANTITY_PATTERN.sub(checked, text)


def _length_values(value: object) -> set[Decimal]:
    if isinstance(value, dict):
        lengths = set()
        for key, item in value.items():
            if key in {'cut_length', 'groove_length', 'cut_length_m', 'groove_length_m'} and type(item) in (int, float):
                lengths.add(Decimal(str(item)))
            elif isinstance(item, (dict, list)):
                lengths.update(_length_values(item))
        return lengths
    if isinstance(value, list):
        return set().union(*(_length_values(item) for item in value if isinstance(item, (dict, list))))
    return set()


def _duration_seconds(value: object) -> set[Decimal]:
    if isinstance(value, dict):
        durations = set()
        for key, item in value.items():
            if key in {'work_time', 'work_time_seconds', 'total_work_time_seconds'} and isinstance(item, (int, float)):
                durations.add(Decimal(str(item)))
            elif isinstance(item, (dict, list)) or key in {'value', 'source'}:
                durations.update(_duration_seconds(item))
        return durations
    if isinstance(value, list):
        return set().union(*(_duration_seconds(item) for item in value if isinstance(item, (dict, list))))
    if isinstance(value, str):
        return {Decimal(hours) * 3600 + Decimal(minutes) * 60 + Decimal(seconds)
                for hours, minutes, seconds in re.findall(r'(?<!\d)(\d+):([0-5]\d):([0-5]\d)(?!\d)', value)}
    return set()


_DURATION_NUMBER = r'\d[\d,]*(?:\.\d+)?'
_DURATION_PATTERN = re.compile(
    rf'(?P<sign>[-−－负]\s*)?(?P<approx>约\s*)?(?:'
    rf'(?P<clock>(?<![\d:T])\d+:\d{{2}}:\d{{2}})(?![\d:])|(?P<duration>'
    rf'{_DURATION_NUMBER}\s*小时(?:\s*{_DURATION_NUMBER}\s*分钟?)?(?:\s*{_DURATION_NUMBER}\s*秒)?|'
    rf'{_DURATION_NUMBER}\s*分钟(?:\s*{_DURATION_NUMBER}\s*秒)?|{_DURATION_NUMBER}\s*秒))'
)


def _check_durations(text: str, durations: set[Decimal]) -> str:
    parts, end = [], 0
    for match in _DURATION_PATTERN.finditer(text):
        if match['sign']:
            raise ValueError('分析时长不能为负数')
        between = text[end:match.start()]
        if match['clock']:
            hours, minutes, seconds = map(Decimal, match['clock'].split(':'))
            if minutes >= 60 or seconds >= 60:
                raise ValueError('分析时长格式无效')
            total, precision = hours * 3600 + minutes * 60 + seconds, Decimal(1)
        else:
            total = Decimal(0)
            for number, unit in re.findall(rf'({_DURATION_NUMBER})\s*(小时|分钟?|秒)', match['duration']):
                amount = Decimal(number.replace(',', ''))
                scale = {'小时': 3600, '分钟': 60, '分': 60, '秒': 1}[unit]
                total += amount * scale
            precision = Decimal(1).scaleb(amount.as_tuple().exponent) * scale
        # Natural-language units express rounded values; clock notation stays exact.
        valid = total in durations if match['clock'] else any(
            (seconds / precision).to_integral_value(rounding=ROUND_HALF_UP) * precision == total
            and abs(total - seconds) <= abs(seconds) * Decimal('0.02')
            for seconds in durations)
        if not valid:
            raise ValueError(f'分析时长与输入不符：生成={(match["clock"] or match["duration"])[:48]}')
        parts.append(between)
        end = match.end()
    return ''.join(parts) + text[end:]


def validate_brief(content: object, context: dict) -> dict:
    try:
        draft = BriefInput.model_validate(content)
    except ValidationError as exc:
        errors = [{'field': '.'.join(str(part)[:32] for part in error['loc'][:4]), 'error': error['type']}
                  for error in exc.errors(include_input=False, include_url=False, include_context=False)[:5]]
        raise ValueError(f'简报结构校验失败：{json.dumps(errors, ensure_ascii=True)}') from None
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
        allowed_evidence = set(candidate['evidence_ids']) & facts.keys()
        evidence = finding.evidence_ids if 'evidence_ids' in finding.model_fields_set else candidate['evidence_ids']
        if not evidence or not set(evidence) <= allowed_evidence:
            received = json.dumps([identifier[:32] for identifier in finding.evidence_ids[:8]], ensure_ascii=True)
            raise ValueError(f'发现包含无效事实引用：candidate={candidate["id"]} allowed={sorted(allowed_evidence)} received={received}')
        selected.add(finding.candidate_id)
        references = context['_references'][finding.candidate_id]
        scope = context['scope']
        if any(ref.get('device_id', '') != scope.get('device_id', '')
               or (ref.get('production_date') and not scope['period_start'] <= ref['production_date'] <= scope['period_end'])
               for ref in references):
            raise ValueError('业务关联不属于当前产线或周期')
        findings.append(dict(**finding.model_dump(exclude={'evidence_ids'}), evidence_ids=evidence,
                             references=references, reference_count=len(references)))
    required = {identifier for identifier, candidate in candidates.items() if candidate.get('required')}
    if not required <= selected:
        raise ValueError(f'必须分析待核查记录：{sorted(required - selected)}，并在该发现正文引用{{{{F020}}}}')
    for finding in draft.findings:
        if candidates[finding.candidate_id].get('required') and '{{F020}}' not in finding.detail:
            raise ValueError('异常分析正文必须引用{{F020}}，完整覆盖各类待核查记录')
    titles = [re.sub(r'\W', '', finding.title) for finding in draft.findings]
    details = [re.sub(r'\W', '', finding.detail) for finding in draft.findings]
    if len(set(titles)) != len(titles) or len(set(details)) != len(details):
        raise ValueError('关键发现重复，请合并同一问题的描述与关联依据')
    for action in draft.actions:
        if action.candidate_id not in selected:
            raise ValueError('建议必须关联真实发现')
        if any(term in action.detail for term in ('加强生产管理', '持续关注相关指标', '进一步提高工作效率')):
            raise ValueError('建议缺少具体跟进对象和动作')
    model_input = model_context(context)
    authoritative = json.dumps(model_input, ensure_ascii=False).replace('％', '%')
    # shortcut: token checks do not establish causality; add targeted checks for concrete false claims.
    allowed_numbers = _numbers(authoritative)
    durations = _duration_seconds(model_input)
    lengths = _length_values(model_input)
    direction_pattern = r'(上升|增长|增加|下降|减少|降低)(?:了)?\s*(?:大约为|约为|大约|约|接近|近)?\s*([+-]?\d[\d,]*(?:\.\d+)?)\s*%'
    def direction_key(direction, amount):
        return (1 if direction in {'上升', '增长', '增加'} else -1, Decimal(amount.replace(',', '')))
    allowed_changes = {direction_key(direction, amount) for direction, amount in re.findall(direction_pattern, authoritative)}
    # Comparison fields are server-computed facts even when prose facts omit their labels.
    comparisons = model_input.get('distributions', {}).get('comparisons', {})
    change_percentages = {Decimal(str(value)) for key, value in comparisons.items()
                          if key.endswith('_percent') and type(value) in (int, float)}
    allowed_changes.update((1 if value > 0 else -1, abs(value)) for value in change_percentages if value != 0)
    allowed_dates = set(re.findall(r'\d{4}-\d{2}-\d{2}', authoritative))
    def render_fact(match: re.Match) -> str:
        identifier = match[1]
        if identifier not in facts:
            raise ValueError(f'正文引用的事实不存在：{identifier[:32]}')
        return str(facts[identifier]['value'])

    result = draft.model_dump(exclude={'findings'})
    prose = [result, *findings, *result['actions']]
    for item in prose:
        for key in ('headline', 'summary', 'title', 'detail'):
            if key in item:
                item[key] = re.sub(r'\{\{(F\d{3})\}\}', render_fact, item[key])
    texts = [prose[0]['headline'], prose[0]['summary']]
    texts += [text for item in prose[1:] for text in (item['title'], item['detail'])]
    for text in texts:
        text = text.replace('％', '%').replace('−', '-').replace('－', '-')
        if not text.strip():
            raise ValueError('分析结论或正文为空')
        if '{{' in text or '}}' in text:
            raise ValueError('正文事实占位符格式无效')
        if re.search(r'\b[FC]\d{3}\b|\bC_[a-z_]+\b', text):
            raise ValueError('正文不应展示内部引用编号')
        for direction, amount in re.findall(direction_pattern, text):
            if not _matches_precision(amount, {value for sign, value in allowed_changes
                                              if sign == direction_key(direction, amount)[0]}):
                raise ValueError(f'分析变化方向或百分比与事实不符：生成={direction}{amount[:32]}%')
        if not set(re.findall(r'\d{4}-\d{2}-\d{2}', text)) <= allowed_dates:
            raise ValueError('分析包含输入中不存在的日期')
        quantities = _check_durations(text, durations)
        quantities = _check_rounded_quantities(quantities, authoritative, change_percentages, lengths)
        quantities = re.sub(r'((?:分(?:为|成)?|按)\s*)[1-3]\s*(?=(?:个\s*)?(?:步|阶段)\s*(?:(?:进行|开展)\s*)?(?:核查|核对|检查|排查))', r'\1', quantities)
        quantities = re.sub(r'((?:建议|请|重点|优先)\s*(?:核查|核对|检查|排查)\s*(?:这|以下)?\s*)[1-3]\s*(?=类|点)', r'\1', quantities)
        if not _numbers(quantities) <= allowed_numbers:
            raise ValueError('分析包含输入中不存在的数量')
    return AnalysisBrief(**result, findings=findings,
                         scope=context['scope'], data_quality=context['data_quality']).model_dump()
