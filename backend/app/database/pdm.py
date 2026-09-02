from __future__ import annotations

from typing import Any, Protocol

from app.config import Settings
from app.database.sqlserver import SqlServerClient
from app.models.conditions import ConditionGroup
from app.services.condition_service import ConditionError, compile_conditions, fields_for, matches_conditions, prepare


class PdmRepository(Protocol):
    mode: str

    def list_projects(self, keyword: str = "", limit: int = 100) -> list[dict[str, Any]]: ...

    def list_catalogs(self, project_id: str) -> list[dict[str, Any]]: ...

    def list_files(
        self,
        project_id: str,
        *,
        catalog_id: str | None,
        status: str,
        keyword: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get_file(self, document_id: str) -> dict[str, Any] | None: ...

    def search_files(self, filters: ConditionGroup, *, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]: ...


class SqlServerPdmRepository:
    mode = "sqlserver"

    FILE_COLUMNS = """
        a.ID AS document_id,
        a.ProjectInfoID AS project_id,
        p.ProjectInfoName AS project_name,
        p.ShipNo AS ship_no,
        a.FileID AS catalog_id,
        c.Title AS catalog_title,
        c.ParentID AS catalog_parent_id,
        c.Type AS catalog_type,
        c.ClassificationType AS classification_type,
        a.VersionID AS version_id,
        v.Version AS version,
        a.DocumentNo AS document_no,
        a.DocumentCName AS document_cname,
        a.DocumentEName AS document_ename,
        a.DocumentState AS document_state,
        a.PBSInfo AS pbs_info,
        a.Operator AS operator,
        a.OperatorJobNo AS operator_job_no,
        a.OperateTime AS operate_time,
        a.Custom1 AS operation_type,
        a.Custom2 AS downloaded_by,
        a.Custom3 AS downloaded_by_job_no,
        a.Custom4 AS downloaded_at,
        a.Custom5 AS downloaded_version,
        a.Custom6 AS custom6,
        physical.physical_file_id,
        physical.physical_version_id,
        physical.VersionNo AS physical_version_no,
        physical.FileType AS physical_file_type,
        physical.FileName AS physical_file_name,
        physical.FileDate AS physical_file_date,
        physical.FileOperator AS physical_file_operator,
        physical.FileOperatorJobNo AS physical_file_operator_job_no,
        physical.PageSize AS physical_page_size,
        physical.VersionCode AS physical_version_code,
        physical.physical_file_count,
        physical.FilePath AS file_path
    """

    FILE_FROM = """
        FROM dbo.PDM_DesignDocumentInfo AS a
        JOIN dbo.PDM_NCDocumentCatalog AS c ON a.FileID = c.ID
        LEFT JOIN dbo.BasicConfig_ProjectInfo AS p ON a.ProjectInfoID = p.ID
        LEFT JOIN dbo.JDDR_PDM_NCDocumentVersion_Data AS v ON a.VersionID = v.ID
        OUTER APPLY (
            SELECT TOP (1)
                f.ID AS physical_file_id,
                f.VersionID AS physical_version_id,
                f.VersionNo,
                f.FileType,
                f.FileName,
                f.FileDate,
                f.FileOperator,
                f.FileOperatorJobNo,
                f.PageSize,
                f.VersionCode,
                COUNT(*) OVER () AS physical_file_count,
                f.FilePath
            FROM dbo.PDM_DocumentFileInfo AS f
            WHERE f.DocumentID = a.ID
              AND (f.VersionID = a.VersionID OR (f.VersionID IS NULL AND a.VersionID IS NULL))
            ORDER BY f.VersionNo DESC, f.FilePath, f.ID
        ) AS physical
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = SqlServerClient(settings, source="file")

    def list_projects(self, keyword: str = "", limit: int = 100) -> list[dict[str, Any]]:
        cleaned = keyword.strip()
        pattern = f"%{cleaned}%"
        return self.client.fetch_all(
            """
            SELECT ID AS project_id,
                   ShipNo AS ship_no,
                   ProjectInfoName AS project_name,
                   State AS state
            FROM dbo.BasicConfig_ProjectInfo
            WHERE State IN (N'待生效', N'规划', N'结束', N'开工', N'生效', N'已进坞', N'已下水')
              AND (? = '' OR ShipNo LIKE ? OR ProjectInfoName LIKE ?)
            ORDER BY ShipNo
            OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY
            """,
            (cleaned, pattern, pattern, limit),
        )

    def list_catalogs(self, project_id: str) -> list[dict[str, Any]]:
        return self.client.fetch_all(
            """
            SELECT ID AS catalog_id,
                   ProjectInfoID AS project_id,
                   ParentID AS parent_id,
                   Type AS catalog_type,
                   Title AS title,
                   SortSN AS sort_sn
            FROM dbo.PDM_NCDocumentCatalog
            WHERE ProjectInfoID = ?
            ORDER BY COALESCE(SortSN, 0), Title
            """,
            (project_id,),
        )

    def _filters(
        self,
        *,
        project_id: str | None = None,
        document_id: str | None = None,
        catalog_id: str | None = None,
        status: str = "all",
        keyword: str = "",
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if project_id:
            clauses.append("a.ProjectInfoID = ?")
            parameters.append(project_id)
        if document_id:
            clauses.append("a.ID = ?")
            parameters.append(document_id)
        if catalog_id:
            clauses.append("a.FileID = ?")
            parameters.append(catalog_id)
        cleaned = keyword.strip()
        if cleaned:
            pattern = f"%{cleaned}%"
            clauses.append(
                "(a.DocumentNo LIKE ? OR a.PBSInfo LIKE ? OR c.Title LIKE ?)"
            )
            parameters.extend((pattern, pattern, pattern))
        if status == "pending":
            clauses.append("COALESCE(LTRIM(RTRIM(a.Custom6)), '') = ''")
        elif status == "sent":
            clauses.append("a.Custom6 = ?")
            parameters.append(self.settings.file_status_sent_value)
        elif status == "failed":
            clauses.append("a.Custom6 = ?")
            parameters.append(self.settings.file_status_failed_value)
        return " AND ".join(clauses) or "1 = 1", parameters

    def list_files(
        self,
        project_id: str,
        *,
        catalog_id: str | None,
        status: str,
        keyword: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        where_sql, parameters = self._filters(
            project_id=project_id,
            catalog_id=catalog_id,
            status=status,
            keyword=keyword,
        )
        total_row = self.client.fetch_all(
            f"""
            SELECT COUNT(*) AS total
            FROM dbo.PDM_DesignDocumentInfo AS a
            JOIN dbo.PDM_NCDocumentCatalog AS c ON a.FileID = c.ID
            WHERE {where_sql}
            """,
            tuple(parameters),
        )[0]
        offset = (page - 1) * page_size
        rows = self.client.fetch_all(
            f"""
            SELECT {self.FILE_COLUMNS}
            {self.FILE_FROM}
            WHERE {where_sql}
            ORDER BY c.Title, a.DocumentNo
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """,
            tuple([*parameters, offset, page_size]),
        )
        return rows, int(total_row["total"] or 0)

    def get_file(self, document_id: str) -> dict[str, Any] | None:
        where_sql, parameters = self._filters(document_id=document_id)
        rows = self.client.fetch_all(
            f"""
            SELECT {self.FILE_COLUMNS}
            {self.FILE_FROM}
            WHERE {where_sql}
            """,
            tuple(parameters),
        )
        return rows[0] if rows else None

    def search_files(self, filters: ConditionGroup, *, page: int, page_size: int):
        if not filters.rules:
            raise ConditionError("请至少添加一条检索条件，不允许无条件检索全部文件")
        where_sql, parameters = compile_conditions(filters, fields_for("cutting_files", self.settings))
        total = self.client.fetch_all(f"SELECT COUNT(*) AS total {self.FILE_FROM} WHERE {where_sql}", tuple(parameters))[0]["total"]
        rows = self.client.fetch_all(
            f"""SELECT {self.FILE_COLUMNS} {self.FILE_FROM} WHERE {where_sql}
                ORDER BY c.Title, a.DocumentNo, a.ID OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""",
            tuple([*parameters, (page - 1) * page_size, page_size]),
        )
        return rows, int(total or 0)


class MockPdmRepository:
    mode = "mock"

    def __init__(self) -> None:
        self.projects = [
            {
                "project_id": "3E97B5EF-0E12-49E2-87E5-B9440C416022",
                "ship_no": "CMYZ0177",
                "project_name": "扬州切割联调项目",
                "state": "生效",
            },
            {
                "project_id": "PRJ-MOCK-002",
                "ship_no": "CMYZ0188",
                "project_name": "文件派发模拟项目",
                "state": "规划",
            },
        ]
        self.catalogs = [
            {
                "catalog_id": "CAT-ROOT",
                "project_id": self.projects[0]["project_id"],
                "parent_id": None,
                "catalog_type": 1,
                "title": "船体专业",
                "sort_sn": 1,
            },
            {
                "catalog_id": "CAT-SEGMENT",
                "project_id": self.projects[0]["project_id"],
                "parent_id": "CAT-ROOT",
                "catalog_type": 1,
                "title": "PK-707C1",
                "sort_sn": 1,
            },
            {
                "catalog_id": "CAT-GEN",
                "project_id": self.projects[0]["project_id"],
                "parent_id": "CAT-SEGMENT",
                "catalog_type": 2,
                "title": "GEN切割文件",
                "sort_sn": 1,
            },
            {
                "catalog_id": "CAT-MOCK-2",
                "project_id": self.projects[1]["project_id"],
                "parent_id": None,
                "catalog_type": 2,
                "title": "模拟文件组",
                "sort_sn": 1,
            },
        ]
        self.files = [
            {
                "document_id": "F1A0796D-7118-4E37-8F69-B6D917BD6456",
                "project_id": self.projects[0]["project_id"],
                "catalog_id": "CAT-GEN",
                "catalog_title": "GEN切割文件",
                "version_id": "VER-MOCK-001",
                "version": "V1.0",
                "document_no": "f7e50f62-d508-430e-8309-8bf309568095.GEN",
                "pbs_info": "PK-707C1",
                "operator": "模拟操作员",
                "operator_job_no": "MOCK001",
                "operate_time": "2026-08-25T10:30:00",
                "operation_type": "上传",
                "custom6": "",
                "physical_version_no": 1,
                "file_path": "CMYZ0177\\PK-707C1\\f7e50f62-d508-430e-8309-8bf309568095.GEN",
            },
            {
                "document_id": "DOC-MOCK-SENT",
                "project_id": self.projects[0]["project_id"],
                "catalog_id": "CAT-GEN",
                "catalog_title": "GEN切割文件",
                "version_id": "VER-MOCK-002",
                "version": "V2.0",
                "document_no": "completed-sample.GEN",
                "pbs_info": "PK-707C1",
                "operator": "模拟操作员",
                "operator_job_no": "MOCK001",
                "operate_time": "2026-08-25T11:00:00",
                "operation_type": "上传",
                "custom6": "已派发",
                "physical_version_no": 2,
                "file_path": "CMYZ0177\\PK-707C1\\completed-sample.GEN",
            },
            {
                "document_id": "DOC-MOCK-FAILED",
                "project_id": self.projects[1]["project_id"],
                "catalog_id": "CAT-MOCK-2",
                "catalog_title": "模拟文件组",
                "version_id": "VER-MOCK-003",
                "version": "V1.0",
                "document_no": "failed-sample.GEN",
                "pbs_info": "MOCK",
                "operator": "模拟操作员",
                "operator_job_no": "MOCK001",
                "operate_time": "2026-08-25T12:00:00",
                "operation_type": "上传",
                "custom6": "派发失败",
                "physical_version_no": 1,
                "file_path": "CMYZ0188\\MOCK\\failed-sample.GEN",
            },
        ]
        catalogs = {item["catalog_id"]: item for item in self.catalogs}
        for item in self.files:
            catalog = catalogs[item["catalog_id"]]
            item.update(
                {
                    "catalog_parent_id": catalog.get("parent_id"),
                    "catalog_type": catalog.get("catalog_type"),
                    "classification_type": "NC",
                    "document_cname": item["document_no"],
                    "document_ename": None,
                    "document_state": "生效",
                    "downloaded_by": None,
                    "downloaded_by_job_no": None,
                    "downloaded_at": None,
                    "downloaded_version": None,
                    "physical_file_id": "PHY-" + item["document_id"],
                    "physical_version_id": item["version_id"],
                    "physical_file_type": "GEN",
                    "physical_file_name": item["file_path"].replace("\\", "/").rsplit("/", 1)[-1],
                    "physical_file_date": item["operate_time"],
                    "physical_file_operator": item["operator"],
                    "physical_file_operator_job_no": item["operator_job_no"],
                    "physical_page_size": None,
                    "physical_version_code": item["version"],
                    "physical_file_count": 1,
                }
            )

    def list_projects(self, keyword: str = "", limit: int = 100) -> list[dict[str, Any]]:
        cleaned = keyword.strip().lower()
        items = [
            item
            for item in self.projects
            if not cleaned
            or cleaned in str(item["ship_no"]).lower()
            or cleaned in str(item["project_name"]).lower()
        ]
        return items[:limit]

    def list_catalogs(self, project_id: str) -> list[dict[str, Any]]:
        return [item for item in self.catalogs if item["project_id"] == project_id]

    def list_files(
        self,
        project_id: str,
        *,
        catalog_id: str | None,
        status: str,
        keyword: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        cleaned = keyword.strip().lower()

        def matches(item: dict[str, Any]) -> bool:
            state = "pending" if not item["custom6"] else "failed" if item["custom6"] == "派发失败" else "sent"
            return (
                item["project_id"] == project_id
                and (not catalog_id or item["catalog_id"] == catalog_id)
                and (status == "all" or state == status)
                and (
                    not cleaned
                    or cleaned in str(item["document_no"]).lower()
                    or cleaned in str(item["pbs_info"]).lower()
                    or cleaned in str(item["catalog_title"]).lower()
                )
            )

        items = [item for item in self.files if matches(item)]
        offset = (page - 1) * page_size
        return items[offset : offset + page_size], len(items)

    def get_file(self, document_id: str) -> dict[str, Any] | None:
        return next((item for item in self.files if item["document_id"] == document_id), None)

    def search_files(self, filters: ConditionGroup, *, page: int, page_size: int):
        if not filters.rules:
            raise ConditionError("请至少添加一条检索条件，不允许无条件检索全部文件")
        fields = fields_for("cutting_files")
        prepare(filters, fields)
        projects = {item["project_id"]: item for item in self.projects}
        rows = []
        for item in self.files:
            project = projects.get(item["project_id"], {})
            row = {**item, "project_name": project.get("project_name"), "ship_no": project.get("ship_no")}
            if matches_conditions(row, filters, fields):
                rows.append(row)
        rows.sort(key=lambda row: (row.get("catalog_title") or "", row.get("document_no") or "", row["document_id"]))
        offset = (page - 1) * page_size
        return rows[offset:offset + page_size], len(rows)
