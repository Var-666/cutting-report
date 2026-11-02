"""Validated SQL compilation and equivalent Mock filtering/ordering."""
import math
from typing import Any

from app.database.condition_fields import sort_fields_for
from app.models.conditions import ConditionError, ConditionGroup, prepare


def prepare_sort(sort, dataset, settings=None):
    allowed = {field.key: field for field in sort_fields_for(dataset, settings)}
    if len(sort) > 5:
        raise ConditionError("最多支持5级排序")
    result, seen = [], set()
    for rule in sort:
        if rule.field not in allowed or rule.direction not in {"asc", "desc"}:
            raise ConditionError("不支持的排序字段或方向")
        if rule.field in seen:
            raise ConditionError("排序字段不能重复")
        seen.add(rule.field)
        result.append((allowed[rule.field], rule.direction.upper()))
    return result


def sort_mock_rows(rows, prepared):
    for field, direction in reversed(prepared):
        def key(row):
            value = row.get(field.source_key or field.key)
            if field.type == "number" and value is not None:
                value = float(value)
            elif value is not None:
                value = str(value).casefold()
            return value is not None, value
        rows.sort(key=key, reverse=direction == "DESC")
    return rows


def _like(value):
    # SQL Server also treats '[' as a LIKE metacharacter. Escape it for literal search.
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_").replace("[", "![")


def compile_conditions(group: ConditionGroup, fields):
    clauses, parameters = [], []
    for field, operator, values in prepare(group, fields):
        column = field.expression
        if operator in {"is_empty", "not_empty"}:
            empty = f"({column} IS NULL OR LTRIM(RTRIM({column})) = '')" if field.type in {"text", "enum"} else f"({column} IS NULL)"
            clauses.append(empty if operator == "is_empty" else f"NOT {empty}")
        elif operator in {"contains", "not_contains", "starts_with", "ends_with"}:
            literal = _like(values[0])
            pattern = ("" if operator == "starts_with" else "%") + literal + ("" if operator == "ends_with" else "%")
            clauses.append(f"{column} {'NOT LIKE' if operator == 'not_contains' else 'LIKE'} ? ESCAPE '!'")
            parameters.append(pattern)
        elif operator in {"in", "not_in"}:
            clauses.append(f"{column} {'NOT IN' if operator == 'not_in' else 'IN'} ({', '.join('?' for _ in values)})")
            parameters.extend(values)
        elif operator == "between":
            clauses.append(f"{column} BETWEEN ? AND ?")
            parameters.extend(values)
        else:
            token = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
            clauses.append(f"{column} {token} ?")
            parameters.extend(values)
    return ("(" + (" AND " if group.logic == "and" else " OR ").join(clauses) + ")" if clauses else "1 = 1"), parameters


def matches_conditions(row: dict[str, Any], group: ConditionGroup, fields):
    """Offline/mock evaluator using the same validation and null semantics."""
    matches = []
    for field, operator, values in prepare(group, fields):
        current = row.get(field.source_key or field.key)
        if field.type == "date" and current is not None:
            current = str(current)[:10]
        if field.type == "number" and current is not None:
            try:
                current = float(current)
                if not math.isfinite(current):
                    current = None
            except (TypeError, ValueError):
                current = None
        if operator in {"is_empty", "not_empty"}:
            empty = current is None or (field.type in {"text", "enum"} and not str(current).strip())
            matches.append(empty if operator == "is_empty" else not empty)
            continue
        if current is None:
            matches.append(False)
            continue
        if field.type in {"text", "enum"}:
            current, values = str(current).casefold(), [str(value).casefold() for value in values]
        value = values[0]
        match operator:
            case "eq": matched = current == value
            case "ne": matched = current != value
            case "contains": matched = value in current
            case "not_contains": matched = value not in current
            case "starts_with": matched = current.startswith(value)
            case "ends_with": matched = current.endswith(value)
            case "in": matched = current in values
            case "not_in": matched = current not in values
            case "between": matched = values[0] <= current <= values[1]
            case "gt": matched = current > value
            case "gte": matched = current >= value
            case "lt": matched = current < value
            case "lte": matched = current <= value
        matches.append(matched)
    return not matches or (all(matches) if group.logic == "and" else any(matches))
