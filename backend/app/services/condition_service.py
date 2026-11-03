"""Public query schema for condition builders."""
from app.database.condition_fields import fields_for, sort_fields_for


def schema_for(dataset, settings=None):
    fields = fields_for(dataset, settings)
    return {"dataset": dataset, "label": {"cutting_files": "PDM 文件", "cutting_downloads": "系统已保存文件"}[dataset],
            "requires_filter": dataset == "cutting_files", "max_rules": 50, "max_values": 50, "max_page_size": 100,
            "fields": [field.public() for field in fields],
            "max_sort_rules": 5,
            "sort_fields": [{"key": field.key, "label": field.label} for field in sort_fields_for(dataset, settings)],
            "logic_options": [{"value": "and", "label": "全部满足（AND）"}, {"value": "or", "label": "任一满足（OR）"}]}
