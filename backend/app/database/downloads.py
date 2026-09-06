from __future__ import annotations

import json

from app.database.sqlite import SQLiteRepository
from app.models.conditions import ConditionGroup
from app.services.condition_service import compile_conditions, fields_for, prepare_sort, sort_expression


class DownloadRepository(SQLiteRepository):
    def initialize(self):
        self.database.initialize()
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS cutting_downloads (
                    download_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('downloading', 'downloaded', 'failed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cutting_downloads_created
                    ON cutting_downloads(created_at DESC, download_id DESC);
            """)

    def save(self, item):
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO cutting_downloads VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(download_id) DO UPDATE SET
                    status=excluded.status, updated_at=excluded.updated_at, payload=excluded.payload
            """, (item["download_id"], item["document_id"], item["status"], item["created_at"],
                  item["updated_at"], json.dumps(item, ensure_ascii=False)))

    def get(self, download_id):
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM cutting_downloads WHERE download_id=?", (download_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def find_latest(self, document_id, version_id, relative_path):
        """Return the newest exact downloaded version without requiring JSON1."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM cutting_downloads
                WHERE document_id=? AND status='downloaded'
                ORDER BY created_at DESC, download_id DESC
                """,
                (document_id,),
            ).fetchall()
        for row in rows:
            item = json.loads(row[0])
            if (
                str(item.get("version_id") or "") == str(version_id or "")
                and str(item.get("relative_path") or "") == str(relative_path or "")
            ):
                return item
        return None

    def list(self, page, page_size, filters=None, sort=()):
        where, parameters = compile_conditions(filters or ConditionGroup(), fields_for("cutting_downloads"))
        prepared = prepare_sort(sort, "cutting_downloads")
        order = ", ".join(f"{sort_expression(field)} {direction}" for field, direction in prepared)
        order = f"{order}, download_id DESC" if order else "created_at DESC, download_id DESC"
        with self.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM cutting_downloads WHERE {where}", parameters).fetchone()[0]
            rows = connection.execute(f"""
                SELECT payload FROM cutting_downloads WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?
            """, (*parameters, page_size, (page - 1) * page_size)).fetchall()
        return [json.loads(row[0]) for row in rows], total

    def recover_interrupted(self, now):
        # Called once at application startup (the deployment uses one worker).
        with self.connect() as connection:
            rows = connection.execute("SELECT payload FROM cutting_downloads WHERE status='downloading'").fetchall()
            for row in rows:
                item = json.loads(row[0])
                item.update(status="failed", updated_at=now, error="服务重启中断下载，请重新下载；此前完成文件不受影响")
                connection.execute("UPDATE cutting_downloads SET status=?, updated_at=?, payload=? WHERE download_id=?",
                                   (item["status"], now, json.dumps(item, ensure_ascii=False), item["download_id"]))
