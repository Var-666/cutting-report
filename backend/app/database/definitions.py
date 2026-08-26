from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.database.sqlite import SQLiteDatabase, SQLiteRepository


DEFAULT_TEMPLATE_CONFIG = {
    "title_prefix": "切割生产",
    "logo_text": "PRODUCTION REPORT",
    "primary_color": "#1f4e78",
    "accent_color": "#56a3a6",
    "background_color": "#eef3f7",
    "layout": "split",
    "table_density": "normal",
    "chart_height": 300,
    "show_meta": True,
}

DEFAULT_QUERY_COLUMNS = [
    {"field": "production_date", "label": "日期", "type": "date", "unit": ""},
    {"field": "category", "label": "分类", "type": "text", "unit": ""},
    {"field": "value", "label": "数量", "type": "number", "unit": ""},
]


class DefinitionRepository(SQLiteRepository):
    def __init__(
        self,
        database: Path | SQLiteDatabase,
        timezone: str = "Asia/Shanghai",
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        super().__init__(database, connection=connection)
        self.timezone = timezone

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).isoformat(timespec="seconds")

    def initialize(self) -> None:
        self.database.initialize()
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS query_tasks (
                    query_task_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    sql_text TEXT NOT NULL,
                    columns_json TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_builtin INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_query_tasks_enabled
                    ON query_tasks(enabled, updated_at DESC);

                CREATE TABLE IF NOT EXISTS report_templates (
                    template_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_builtin INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_definitions (
                    definition_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    query_task_id TEXT,
                    template_id TEXT NOT NULL,
                    period_type TEXT NOT NULL DEFAULT 'day',
                    visual_config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_builtin INTEGER NOT NULL DEFAULT 0,
                    builtin_kind TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(query_task_id) REFERENCES query_tasks(query_task_id),
                    FOREIGN KEY(template_id) REFERENCES report_templates(template_id)
                );
                CREATE INDEX IF NOT EXISTS idx_report_definitions_enabled
                    ON report_definitions(enabled, updated_at DESC);
                """
            )
            now = self._now()
            connection.execute(
                """
                INSERT OR IGNORE INTO report_templates (
                    template_id, code, name, description, config_json,
                    enabled, is_builtin, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, 1, 1, ?, ?)
                """,
                (
                    "tpl_classic_blue",
                    "classic_blue",
                    "经典蓝色生产报表",
                    "系统内置的表格与图表双栏模板",
                    json.dumps(DEFAULT_TEMPLATE_CONFIG, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            for definition_id, code, name, period_type in (
                ("def_leadership", "leadership", "领导报表", "month"),
                ("def_workshop", "workshop", "车间报表", "day"),
                ("def_equipment", "equipment", "设备报表", "day"),
            ):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO report_definitions (
                        definition_id, code, name, description, query_task_id,
                        template_id, period_type, visual_config_json, enabled,
                        is_builtin, builtin_kind, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, 'tpl_classic_blue', ?, '{}', 1, 1, ?, 1, ?, ?)
                    """,
                    (
                        definition_id,
                        code,
                        name,
                        "系统内置报表，保留当前专用统计和版式",
                        period_type,
                        code,
                        now,
                        now,
                    ),
                )

    @staticmethod
    def _decode(row: sqlite3.Row | None, json_fields: dict[str, str]) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for source, target in json_fields.items():
            item[target] = json.loads(item.pop(source) or "null")
        item["enabled"] = bool(item["enabled"])
        item["is_builtin"] = bool(item["is_builtin"])
        return item

    def list_query_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM query_tasks ORDER BY is_builtin DESC, updated_at DESC"
            ).fetchall()
        return [self._decode(row, {"columns_json": "columns"}) for row in rows]

    def get_query_task(self, query_task_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM query_tasks WHERE query_task_id = ?", (query_task_id,)
            ).fetchone()
        return self._decode(row, {"columns_json": "columns"})

    def save_query_task(self, payload: dict[str, Any], query_task_id: str | None = None) -> dict[str, Any]:
        now = self._now()
        query_task_id = query_task_id or f"qry_{uuid.uuid4().hex[:12]}"
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT is_builtin FROM query_tasks WHERE query_task_id = ?", (query_task_id,)
            ).fetchone()
            if existing and existing["is_builtin"]:
                raise PermissionError("系统内置查询任务不能修改")
            if existing:
                connection.execute(
                    """
                    UPDATE query_tasks SET code=?, name=?, description=?, sql_text=?,
                        columns_json=?, enabled=?, version=version+1, updated_at=?
                    WHERE query_task_id=?
                    """,
                    (
                        payload["code"], payload["name"], payload.get("description", ""),
                        payload["sql_text"], json.dumps(payload.get("columns") or DEFAULT_QUERY_COLUMNS, ensure_ascii=False),
                        int(payload.get("enabled", True)), now, query_task_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO query_tasks (
                        query_task_id, code, name, description, sql_text, columns_json,
                        enabled, is_builtin, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                    """,
                    (
                        query_task_id, payload["code"], payload["name"], payload.get("description", ""),
                        payload["sql_text"], json.dumps(payload.get("columns") or DEFAULT_QUERY_COLUMNS, ensure_ascii=False),
                        int(payload.get("enabled", True)), now, now,
                    ),
                )
        return self.get_query_task(query_task_id)

    def delete_query_task(self, query_task_id: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT is_builtin FROM query_tasks WHERE query_task_id=?", (query_task_id,)
            ).fetchone()
            if not row:
                raise KeyError(query_task_id)
            if row["is_builtin"]:
                raise PermissionError("系统内置查询任务不能删除")
            used = connection.execute(
                "SELECT COUNT(*) FROM report_definitions WHERE query_task_id=?", (query_task_id,)
            ).fetchone()[0]
            if used:
                raise ValueError("查询任务正在被报表定义使用")
            connection.execute("DELETE FROM query_tasks WHERE query_task_id=?", (query_task_id,))

    def list_templates(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM report_templates ORDER BY is_builtin DESC, updated_at DESC"
            ).fetchall()
        return [self._decode(row, {"config_json": "config"}) for row in rows]

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_templates WHERE template_id=?", (template_id,)
            ).fetchone()
        return self._decode(row, {"config_json": "config"})

    def save_template(self, payload: dict[str, Any], template_id: str | None = None) -> dict[str, Any]:
        now = self._now()
        template_id = template_id or f"tpl_{uuid.uuid4().hex[:12]}"
        config = {**DEFAULT_TEMPLATE_CONFIG, **(payload.get("config") or {})}
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT is_builtin FROM report_templates WHERE template_id=?", (template_id,)
            ).fetchone()
            if existing and existing["is_builtin"]:
                raise PermissionError("系统内置模板不能修改，请先复制")
            if existing:
                connection.execute(
                    """
                    UPDATE report_templates SET code=?, name=?, description=?, config_json=?,
                        enabled=?, version=version+1, updated_at=? WHERE template_id=?
                    """,
                    (
                        payload["code"], payload["name"], payload.get("description", ""),
                        json.dumps(config, ensure_ascii=False), int(payload.get("enabled", True)), now, template_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO report_templates (
                        template_id, code, name, description, config_json, enabled,
                        is_builtin, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                    """,
                    (
                        template_id, payload["code"], payload["name"], payload.get("description", ""),
                        json.dumps(config, ensure_ascii=False), int(payload.get("enabled", True)), now, now,
                    ),
                )
        return self.get_template(template_id)

    def delete_template(self, template_id: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT is_builtin FROM report_templates WHERE template_id=?", (template_id,)
            ).fetchone()
            if not row:
                raise KeyError(template_id)
            if row["is_builtin"]:
                raise PermissionError("系统内置模板不能删除")
            used = connection.execute(
                "SELECT COUNT(*) FROM report_definitions WHERE template_id=?", (template_id,)
            ).fetchone()[0]
            if used:
                raise ValueError("模板正在被报表定义使用")
            connection.execute("DELETE FROM report_templates WHERE template_id=?", (template_id,))

    def list_definitions(self, *, enabled_only: bool = False, include_builtin: bool = True) -> list[dict[str, Any]]:
        clauses = []
        if enabled_only:
            clauses.append("d.enabled=1")
        if not include_builtin:
            clauses.append("d.is_builtin=0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT d.*, q.name AS query_task_name, t.name AS template_name
                FROM report_definitions d
                LEFT JOIN query_tasks q ON q.query_task_id=d.query_task_id
                JOIN report_templates t ON t.template_id=d.template_id
                {where}
                ORDER BY d.is_builtin DESC, d.updated_at DESC
                """
            ).fetchall()
        return [self._decode(row, {"visual_config_json": "visual_config"}) for row in rows]

    def get_definition(self, definition_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, q.name AS query_task_name, t.name AS template_name
                FROM report_definitions d
                LEFT JOIN query_tasks q ON q.query_task_id=d.query_task_id
                JOIN report_templates t ON t.template_id=d.template_id
                WHERE d.definition_id=?
                """,
                (definition_id,),
            ).fetchone()
        return self._decode(row, {"visual_config_json": "visual_config"})

    def save_definition(self, payload: dict[str, Any], definition_id: str | None = None) -> dict[str, Any]:
        now = self._now()
        definition_id = definition_id or f"def_{uuid.uuid4().hex[:12]}"
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT is_builtin FROM report_definitions WHERE definition_id=?", (definition_id,)
            ).fetchone()
            if existing and existing["is_builtin"]:
                raise PermissionError("系统内置报表定义不能修改")
            query_exists = connection.execute(
                "SELECT 1 FROM query_tasks WHERE query_task_id=? AND enabled=1", (payload["query_task_id"],)
            ).fetchone()
            template_exists = connection.execute(
                "SELECT 1 FROM report_templates WHERE template_id=? AND enabled=1", (payload["template_id"],)
            ).fetchone()
            if not query_exists or not template_exists:
                raise ValueError("请选择已启用的查询任务和模板")
            values = (
                payload["code"], payload["name"], payload.get("description", ""),
                payload["query_task_id"], payload["template_id"], payload.get("period_type", "day"),
                json.dumps(payload.get("visual_config") or {}, ensure_ascii=False),
                int(payload.get("enabled", True)), now,
            )
            if existing:
                connection.execute(
                    """
                    UPDATE report_definitions SET code=?, name=?, description=?, query_task_id=?,
                        template_id=?, period_type=?, visual_config_json=?, enabled=?,
                        version=version+1, updated_at=? WHERE definition_id=?
                    """,
                    (*values, definition_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO report_definitions (
                        definition_id, code, name, description, query_task_id, template_id,
                        period_type, visual_config_json, enabled, is_builtin, builtin_kind,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, 1, ?, ?)
                    """,
                    (definition_id, *values[:-1], now, now),
                )
        return self.get_definition(definition_id)

    def delete_definition(self, definition_id: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT is_builtin FROM report_definitions WHERE definition_id=?", (definition_id,)
            ).fetchone()
            if not row:
                raise KeyError(definition_id)
            if row["is_builtin"]:
                raise PermissionError("系统内置报表定义不能删除")
            connection.execute("DELETE FROM report_definitions WHERE definition_id=?", (definition_id,))
