"""Public query schema and compatibility exports for existing service callers."""
from app.database.condition_fields import (
    fields_for as fields_for,
    sort_expression as sort_expression,
    sort_fields_for as sort_fields_for,
)
from app.database.condition_query import (
    compile_conditions as compile_conditions,
    matches_conditions as matches_conditions,
    prepare_sort as prepare_sort,
    sort_mock_rows as sort_mock_rows,
)
from app.models.conditions import (
    ConditionError as ConditionError,
    FieldSpec as FieldSpec,
    OPERATORS as OPERATORS,
    TEXT_OPS as TEXT_OPS,
    ORDERED_OPS as ORDERED_OPS,
    ENUM_OPS as ENUM_OPS,
    prepare as prepare,
)


def schema_for(dataset, settings=None):
    fields = fields_for(dataset, settings)
    return {"dataset": dataset, "label": {"cutting_files": "PDM 文件", "cutting_downloads": "系统已保存文件"}[dataset],
            "requires_filter": dataset == "cutting_files", "max_rules": 50, "max_values": 50, "max_page_size": 100,
            "fields": [field.public() for field in fields],
            "max_sort_rules": 5,
            "sort_fields": [{"key": field.key, "label": field.label} for field in sort_fields_for(dataset, settings)],
            "logic_options": [{"value": "and", "label": "全部满足（AND）"}, {"value": "or", "label": "任一满足（OR）"}]}
