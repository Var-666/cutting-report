from dataclasses import replace
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.api.auth import optional_user
from app.api.conditions import router as conditions_router
from app.api.cutting import router as cutting_router
from app.api.downloads import router as downloads_router
from app.config import settings
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository, SqlServerPdmRepository
from app.models.conditions import ConditionGroup, ConditionSearch, SortRule
from app.services.condition_service import ConditionError, compile_conditions, fields_for, matches_conditions, prepare, schema_for
from app.services.cutting_dispatch_service import CuttingDispatchService
from app.services.file_download_service import FileDownloadService
from app.services.file_source import DisabledFileSource


def rule(field, operator, value=None):
    return {"field": field, "operator": operator, "value": value}


def group(*rules, logic="and"):
    return ConditionGroup.model_validate({"logic": logic, "rules": list(rules)})


@pytest.mark.parametrize("dataset", ["cutting_files", "cutting_downloads"])
def test_schemas_only_expose_allowed_fields_not_sql(dataset):
    schema = schema_for(dataset)
    assert schema["max_rules"] == 50 and schema["max_page_size"] == 100
    assert schema["requires_filter"] == (dataset == "cutting_files")
    assert len({field["key"] for field in schema["fields"]}) == len(schema["fields"])
    assert all("expression" not in field and "source_key" not in field for field in schema["fields"])
    assert all(field["group"] for field in schema["fields"])


def test_registry_supports_configured_status_values():
    cfg = replace(settings, file_status_sent_value="SENT", file_status_failed_value="FAILED")
    fields = fields_for("cutting_files", cfg)
    where, params = compile_conditions(group(rule("dispatch_state", "eq", "SENT")), fields)
    assert "a.Custom6 = ?" in where and params == ["SENT"]
    with pytest.raises(ConditionError):
        prepare(group(rule("dispatch_state", "eq", "random")), fields)


def test_project_id_field_follows_pdm_document_mapping():
    fields = fields_for("cutting_files")
    project_field = next(field for field in fields if field.key == "project_id")
    assert project_field.label == "项目ID（ProjectInfoID）"

    project_id = "3E97B5EF-0E12-49E2-87E5-B9440C416022"
    where, params = compile_conditions(
        group(rule("project_id", "eq", project_id)),
        fields,
    )
    assert where == "(a.ProjectInfoID = ?)"
    assert params == [project_id]


@pytest.mark.parametrize(
    "field,operator,value,sql_expression",
    [
        ("catalog_id", "eq", "A00251A4-120B-4036-9F97-07A85793E891", "a.FileID = ?"),
        ("catalog_title", "contains", "GEN", "c.Title LIKE ?"),
        ("version_id", "eq", "VER-ID", "a.VersionID = ?"),
        ("operation_type", "eq", "上传", "a.Custom1 = ?"),
        ("downloaded_by", "contains", "张三", "a.Custom2 LIKE ?"),
        ("downloaded_date", "eq", "2026-08-25", "TRY_CONVERT(date, a.Custom4) = ?"),
        ("physical_file_name", "ends_with", ".GEN", "physical.FileName LIKE ?"),
        ("physical_file_type", "eq", "GEN", "physical.FileType = ?"),
        ("physical_version_code", "eq", "V1", "physical.VersionCode = ?"),
    ],
)
def test_original_yangzhou_sql_fields_are_available_as_safe_conditions(
    field, operator, value, sql_expression
):
    where, params = compile_conditions(
        group(rule(field, operator, value)), fields_for("cutting_files")
    )
    assert sql_expression in where
    assert params


def test_file_condition_labels_follow_original_sql_business_names():
    fields = fields_for("cutting_files")
    labels = {field.key: field.label for field in fields}
    assert fields[0].key == "project_id"
    assert {"ship_no", "project_name", "physical_file_count"}.isdisjoint(labels)
    assert labels["catalog_id"] == "文件分类 / 分组ID（FileID）"
    assert labels["catalog_title"] == "文件分类 / 分组名称（Title）"
    assert labels["document_no"] == "文件名（DocumentNo）"
    assert labels["pbs_info"] == "中间产品 / 分段（PBSInfo）"
    assert labels["document_id"] == "设计文档ID（ID）"
    assert labels["operate_date"] == "操作日期（OperateTime）"
    assert labels["dispatch_state"] == "派发状态（Custom6）"
    assert labels["physical_file_id"] == "物理文件ID（ID）"
    assert labels["file_path"] == "文件相对路径（FilePath）"
    assert all("（" in label and label.endswith("）") for label in labels.values())


@pytest.mark.parametrize("payload", [
    {"page": 0}, {"page_size": 101}, {"page": "1"}, {"page": True},
    {"sql": "SELECT * FROM secrets"},
    {"filters": {"logic": "xor"}}, {"filters": {"rules": [rule("document_no", "eq", "a")] * 51}},
    {"filters": {"rules": [{**rule("document_no", "eq", "a"), "expression": "a.ID"}]}},
    {"filters": {"rules": [rule("document_no", "eq", {"sql": "x"})]}},
    {"filters": {"rules": [rule("document_no", "eq", True)]}},
    {"filters": {"rules": [rule("document_no", "in", [False])]}},
])
def test_strict_bounded_contract(payload):
    with pytest.raises(ValidationError):
        ConditionSearch.model_validate(payload)


@pytest.mark.parametrize("invalid", [
    rule("a.DocumentNo", "eq", "x"), rule("document_no;DROP TABLE x", "eq", "x"),
    rule("document_no", "raw_sql", "x"), rule("document_no", "between", ["a", "z"]),
    rule("physical_version_no", "contains", "1"), rule("dispatch_state", "eq", "unknown"),
    rule("operate_date", "eq", "2026-02-30"), rule("operate_date", "eq", "20260831"),
    rule("operate_date", "eq", "2101-01-01"), rule("operate_date", "between", ["2026-09-01", "2026-08-01"]),
    rule("physical_version_no", "between", [2, 1]), rule("physical_version_no", "between", [1]),
    rule("physical_version_no", "eq", "nan"), rule("physical_version_no", "eq", "inf"),
    rule("physical_version_no", "eq", 1e15), rule("physical_version_no", "eq", "abc"),
    rule("document_no", "eq", "  "), rule("document_no", "eq", "a" * 501),
    rule("document_no", "eq", None), rule("document_no", "eq", ["a"]),
    rule("document_no", "eq", 1), rule("document_no", "in", []),
    rule("document_no", "in", ["a"] * 51), rule("document_no", "is_empty", "x"),
])
def test_invalid_conditions_rejected_before_query(invalid):
    with pytest.raises(ConditionError):
        compile_conditions(group(invalid), fields_for("cutting_files"))


def test_total_bind_values_bound_for_sqlserver_and_fifty_rules_supported():
    fields = fields_for("cutting_files")
    assert len(compile_conditions(group(*[rule("document_no", "eq", "x")] * 50), fields)[1]) == 50
    with pytest.raises(ConditionError, match="1000"):
        compile_conditions(group(*[rule("document_no", "in", ["x"] * 50)] * 21), fields)


def test_sql_injection_is_only_a_bound_literal_and_like_wildcards_escaped():
    attack = "x%' OR 1=1; DROP TABLE files; --_[!"
    where, params = compile_conditions(group(rule("document_no", "contains", attack)), fields_for("cutting_files"))
    assert where == "(a.DocumentNo LIKE ? ESCAPE '!')"
    assert attack not in where and params == ["%x!%' OR 1=1; DROP TABLE files; --!_![!!%"]


def test_mock_and_or_pagination_and_cross_project_search():
    pdm = MockPdmRepository()
    first_project = pdm.projects[0]["project_id"]
    second_project = pdm.projects[1]["project_id"]
    both = group(rule("project_id", "eq", first_project), rule("dispatch_state", "is_empty"))
    rows, total = pdm.search_files(both, page=1, page_size=1)
    assert total == 1 and rows[0]["project_id"] == first_project and rows[0]["custom6"] == ""
    either = group(rule("project_id", "eq", first_project), rule("project_id", "eq", second_project), logic="or")
    first, total = pdm.search_files(either, page=1, page_size=2)
    second, total2 = pdm.search_files(either, page=2, page_size=2)
    assert total == total2 == 3 and len(first) == 2 and len(second) == 1
    assert len({row["document_id"] for row in first + second}) == 3
    rows, total = pdm.search_files(group(rule("project_id", "eq", "not-a-project")), page=1, page_size=10)
    assert rows == [] and total == 0


@pytest.mark.parametrize("operator,value,expected", [
    ("eq", 0, True), ("ne", 0, False), ("between", [0, 1], True),
    ("gte", "0", True), ("gt", 0, False), ("lt", 1, True), ("lte", 0, True),
    ("in", [0, 2], True), ("not_in", [1, 2], True), ("is_empty", None, False),
])
def test_numeric_zero_and_boundaries(operator, value, expected):
    assert matches_conditions({"size": 0}, group(rule("size", operator, value)), fields_for("cutting_downloads")) is expected


@pytest.mark.parametrize("operator,value,expected", [
    ("eq", "x", False), ("ne", "x", False), ("not_in", ["x"], False),
    ("not_contains", "x", False), ("is_empty", None, True), ("not_empty", None, False),
])
def test_null_matches_sql_semantics(operator, value, expected):
    assert matches_conditions({"filename": None}, group(rule("filename", operator, value)), fields_for("cutting_downloads")) is expected


class SqlClient:
    def __init__(self):
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, params))
        return [{"total": 7}] if sql.startswith("SELECT COUNT(*) AS total") else []


def test_pdm_project_and_catalog_queries_are_cached(monkeypatch):
    now = [0]
    monkeypatch.setattr("app.database.pdm.monotonic", lambda: now[0])
    pdm = SqlServerPdmRepository(settings)

    class DirectoryClient:
        def __init__(self):
            self.calls = []

        def fetch_all(self, sql, params):
            self.calls.append((sql, params))
            if "BasicConfig_ProjectInfo" in sql:
                return [{"project_id": "P1", "ship_no": "S1", "project_name": "项目一", "state": "生效"}]
            return [{"catalog_id": "C1", "project_id": "P1", "parent_id": None, "catalog_type": "folder", "title": "船体", "sort_sn": 1}]

    pdm.client = DirectoryClient()
    first_projects = pdm.list_projects("", 1000)
    second_projects = pdm.list_projects("", 1000)
    first_catalogs = pdm.list_catalogs("P1")
    second_catalogs = pdm.list_catalogs("P1")

    assert first_projects == second_projects
    assert first_catalogs == second_catalogs
    assert len(pdm.client.calls) == 2
    project_sql, project_parameters = pdm.client.calls[0]
    assert "ShipNo LIKE" not in project_sql
    assert project_parameters == (1000,)
    first_projects[0]["project_name"] = "页面修改不会污染缓存"
    assert pdm.list_projects("", 1000)[0]["project_name"] == "项目一"
    now[0] = pdm.DIRECTORY_CACHE_SECONDS + 1
    pdm.list_projects("", 1000)
    pdm.list_catalogs("P1")
    assert len(pdm.client.calls) == 4


def test_pdm_directory_page_uses_one_windowed_query():
    pdm = SqlServerPdmRepository(settings)

    class PageClient:
        def __init__(self):
            self.calls = []

        def fetch_all(self, sql, params):
            self.calls.append((sql, params))
            return [{"document_id": "D1", "total_count": 7}]

    pdm.client = PageClient()
    rows, total = pdm.list_files(
        "P1",
        catalog_id="C1",
        status="all",
        keyword="",
        page=1,
        page_size=20,
    )

    assert total == 7
    assert rows == [{"document_id": "D1"}]
    assert len(pdm.client.calls) == 1
    sql, parameters = pdm.client.calls[0]
    assert "COUNT_BIG(*) OVER () AS total_count" in sql
    assert "OUTER APPLY" in sql.split("ROWS ONLY", 1)[1]
    assert parameters == ("P1", "C1", 0, 20)


def test_sqlserver_count_and_page_use_identical_safe_filter():
    pdm = SqlServerPdmRepository(settings)
    pdm.client = SqlClient()
    project_id = "3E97B5EF-0E12-49E2-87E5-B9440C416022"
    criteria = group(rule("project_id", "eq", project_id), rule("operate_date", "between", ["2026-08-01", "2026-08-31"]), logic="or")
    assert pdm.search_files(criteria, page=3, page_size=2) == ([], 7)
    count, page = pdm.client.calls
    where, params = compile_conditions(criteria, fields_for("cutting_files", settings))
    assert f"WHERE {where}" in count[0] and f"WHERE {where}" in page[0]
    assert count[1] == tuple(params) and page[1] == (*params, 4, 2)
    assert "ORDER BY c.Title ASC, a.DocumentNo ASC, a.ID ASC" in page[0]
    assert "OUTER APPLY" not in count[0] and "f.VersionID = a.VersionID" in page[0]
    assert "OUTER APPLY" not in page[0].split("ROWS ONLY")[0]
    assert project_id not in count[0]


def test_pdm_pages_share_count_but_new_search_and_expiry_recount(monkeypatch):
    now = [0]
    monkeypatch.setattr("app.database.pdm.monotonic", lambda: now[0])
    pdm = SqlServerPdmRepository(settings)
    pdm.client = SqlClient()
    criteria = group(rule("document_no", "contains", ".GEN"))
    pdm.search_files(criteria, page=1, page_size=10)
    pdm.search_files(criteria, page=2, page_size=10)
    pdm.search_files(criteria, page=3, page_size=10)
    assert sum(sql.startswith("SELECT COUNT") for sql, _ in pdm.client.calls) == 1
    pdm.search_files(criteria, page=1, page_size=10)
    assert sum(sql.startswith("SELECT COUNT") for sql, _ in pdm.client.calls) == 2
    now[0] = 61
    pdm.search_files(criteria, page=2, page_size=10)
    pdm.search_files(group(rule("document_no", "contains", ".pdf")), page=2, page_size=10)
    assert sum(sql.startswith("SELECT COUNT") for sql, _ in pdm.client.calls) == 4


@pytest.mark.parametrize("logic", ["and", "or"])
def test_physical_filters_keep_version_matched_top_file_semantics(logic):
    pdm = SqlServerPdmRepository(settings)
    pdm.client = SqlClient()
    criteria = group(rule("document_no", "contains", ".GEN"), rule("physical_file_name", "eq", "abc.gen"), logic=logic)
    pdm.search_files(criteria, page=1, page_size=10)
    count, page = pdm.client.calls
    where, params = compile_conditions(criteria, fields_for("cutting_files", settings))
    for sql, _ in (count, page):
        assert f"WHERE {where}" in sql
        assert "TOP (1)" in sql and "f.VersionID = a.VersionID" in sql
        assert "ORDER BY f.VersionNo DESC, f.FilePath, f.ID" in sql
    assert count[1] == tuple(params)
    assert page[1] == (*params, 0, 10)


def test_sqlserver_sort_is_multilevel_and_has_unique_tiebreaker():
    pdm = SqlServerPdmRepository(settings)
    pdm.client = SqlClient()
    pdm.search_files(group(rule("project_id", "eq", "project")), page=2, page_size=10,
                     sort=[SortRule(field="operate_date", direction="desc"), SortRule(field="document_no")])
    count, page = pdm.client.calls
    assert "ORDER BY a.OperateTime DESC, a.DocumentNo ASC, a.ID ASC" in page[0]
    assert "ORDER BY page.sort_0 DESC, page.sort_1 ASC, page.document_id ASC" in page[0]
    assert "a.OperateTime AS sort_0" in page[0]
    assert "OUTER APPLY" not in count[0]


@pytest.mark.parametrize("field,dependency", [("version", "JDDR_PDM_NCDocumentVersion_Data"), ("physical_version_no", "OUTER APPLY")])
def test_sort_only_joins_are_not_added_to_count(field, dependency):
    pdm = SqlServerPdmRepository(settings)
    pdm.client = SqlClient()
    pdm.search_files(group(rule("document_no", "contains", "GEN")), page=1, page_size=10, sort=[SortRule(field=field)])
    count, page = pdm.client.calls
    assert dependency not in count[0]
    assert dependency in page[0].split("ROWS ONLY")[0]


@pytest.mark.parametrize("sort", [
    [{"field": "document_no; DROP TABLE x", "direction": "asc"}],
    [{"field": "unknown", "direction": "asc"}],
    [{"field": "document_no", "direction": "asc"}] * 2,
])
def test_invalid_sort_never_reaches_database(sort):
    pdm = SqlServerPdmRepository(settings)
    pdm.client = SqlClient()
    with pytest.raises(ConditionError):
        pdm.search_files(group(rule("document_no", "contains", "GEN")), page=1, page_size=10,
                         sort=[SortRule.model_validate(item) for item in sort])
    assert pdm.client.calls == []


@pytest.mark.parametrize("sort", [
    [{"field": "document_no", "direction": "asc; DROP TABLE x"}],
    [{"field": "document_no", "expression": "a.ID"}],
    [{"field": "document_no"}] * 6,
])
def test_sort_contract_rejects_injection_and_unbounded_rules(sort):
    with pytest.raises(ValidationError):
        ConditionSearch.model_validate({"sort": sort})


def test_mock_sort_is_before_pagination_and_ties_are_deterministic():
    pdm = MockPdmRepository()
    criteria = group(rule("document_no", "contains", ".GEN"))
    sort = [SortRule(field="operate_date", direction="desc"), SortRule(field="document_no")]
    full, total = pdm.search_files(criteria, page=1, page_size=100, sort=sort)
    first, _ = pdm.search_files(criteria, page=1, page_size=1, sort=sort)
    second, _ = pdm.search_files(criteria, page=2, page_size=1, sort=sort)
    assert total == 3
    assert first + second == full[:2]
    assert full[0]["operate_time"] >= full[1]["operate_time"] >= full[2]["operate_time"]
    assert first[0]["document_id"] != second[0]["document_id"]


def test_empty_or_invalid_pdm_query_does_not_access_database():
    for pdm in [MockPdmRepository(), SqlServerPdmRepository(settings)]:
        pdm.client = SqlClient()
        for criteria in [group(), group(rule("not_allowed", "eq", "x"))]:
            with pytest.raises(ConditionError):
                pdm.search_files(criteria, page=1, page_size=10)
        assert pdm.client.calls == []


@pytest.fixture
def downloads(tmp_path):
    repo = DownloadRepository(tmp_path / "filters.db")
    repo.initialize()
    for index, (filename, status, day, size) in enumerate([
        ("甲板_%[!.gen", "downloaded", "2026-08-01", 0),
        ("加强筋.gen", "downloaded", "2026-08-31", 2048),
        ("甲板新版本.gen", "failed", "2026-09-01", None),
    ]):
        repo.save({"download_id": str(index), "document_id": str(index), "filename": filename,
                   "document_no": filename, "status": status, "created_at": day + "T12:00:00+08:00",
                   "updated_at": day, "size": size, "source_mode": "mock", "version": "V1",
                   "project_id": "P1" if index < 2 else "P2",
                   "catalog_id": "CAT-GEN" if index < 2 else "CAT-NC"})
    return repo


def test_sqlite_and_or_date_and_numeric_pagination(downloads):
    criteria = group(rule("download_date", "between", ["2026-08-01", "2026-08-31"]), rule("size", "gte", 0))
    rows, total = downloads.list(1, 1, criteria)
    assert total == 2 and rows[0]["download_id"] == "1"
    assert downloads.list(2, 1, criteria)[0][0]["download_id"] == "0"
    either = group(rule("status", "eq", "failed"), rule("size", "eq", 0), logic="or")
    assert downloads.list(1, 10, either)[1] == 2
    assert downloads.list(1, 10, group(rule("size", "is_empty")))[0][0]["download_id"] == "2"
    assert downloads.list(1, 10, group())[1] == 3


def test_download_sort_is_numeric_and_applied_before_pagination(downloads):
    sort = [SortRule(field="size", direction="desc"), SortRule(field="download_date")]
    first, total = downloads.list(1, 1, sort=sort)
    second, _ = downloads.list(2, 1, sort=sort)
    assert total == 3
    assert first[0]["download_id"] == "1"
    assert second[0]["download_id"] == "0"
    with pytest.raises(ConditionError):
        downloads.list(1, 1, sort=[SortRule(field="created_at;DROP")])


def test_downloads_filter_by_saved_project_and_catalog_metadata(downloads):
    criteria = group(rule("project_id", "eq", "P1"), rule("catalog_id", "eq", "CAT-GEN"))
    rows, total = downloads.list(1, 20, criteria)
    assert total == 2
    assert {row["download_id"] for row in rows} == {"0", "1"}


@pytest.mark.parametrize("operator,value,expected", [
    ("contains", "_%[!", 1), ("not_contains", "_%[!", 2),
    ("starts_with", "甲板_", 1), ("ends_with", "!.gen", 1),
    ("eq", "' OR 1=1 --", 0), ("in", ["加强筋.gen"], 1),
])
def test_sqlite_literal_text_operators(downloads, operator, value, expected):
    assert downloads.list(1, 10, group(rule("filename", operator, value)))[1] == expected
    assert downloads.list(1, 10)[1] == 3


@pytest.fixture
def api(tmp_path):
    cfg = replace(settings, project_root=tmp_path)
    pdm = MockPdmRepository()
    repo = DownloadRepository(tmp_path / "api.db")
    repo.initialize()
    app = FastAPI()
    app.include_router(conditions_router)
    app.include_router(cutting_router)
    app.include_router(downloads_router)
    app.state.cutting_dispatch = CuttingDispatchService(cfg, pdm, SimpleNamespace(), DisabledFileSource())
    app.state.file_downloads = FileDownloadService(cfg, repo, pdm, DisabledFileSource())
    app.dependency_overrides[optional_user] = lambda: {"role": "admin", "user_id": "tests"}

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "conditions-test"
        return await call_next(request)

    return app, TestClient(app), repo


def test_api_search_and_schema_are_readonly_and_paged(api):
    app, client, repo = api
    response = client.get("/api/v1/conditions/cutting_files/schema")
    assert response.status_code == 200 and response.json()["data"]["requires_filter"]
    payload = {"filters": group(rule("project_id", "eq", app.state.cutting_dispatch.pdm.projects[0]["project_id"])).model_dump(), "page_size": 1}
    response = client.post("/api/v1/cutting/files/search", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2 and len(data["items"]) == 1 and data["items"][0]["path_available"]
    assert repo.list(1, 10)[1] == 0
    assert client.post("/api/v1/cutting/files/search", json={}).status_code == 422
    assert client.get("/api/v1/conditions/private/schema").status_code == 404
    response = client.post("/api/v1/cutting/downloads/search", json={})
    assert response.status_code == 200 and response.json()["data"]["total"] == 0
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/v1/cutting/files/search", "/api/v1/cutting/downloads/search"])
def test_api_rejects_invalid_conditions_and_pages(api, path):
    _, client, _ = api
    assert client.post(path, json={"page_size": 1000}).status_code == 422
    assert client.post(path, json={"filters": group(rule("private_column", "eq", "x")).model_dump()}).status_code == 422
    assert client.post(path, json={"filters": group(rule("document_no", "contains", "GEN")).model_dump(), "sort": [{"field": "not_allowed"}]}).status_code == 422


def test_api_passes_sort_to_repository_before_paging(api):
    _, client, _ = api
    base = {"filters": group(rule("document_no", "contains", "GEN")).model_dump(), "page_size": 1}
    names = []
    for direction in ("asc", "desc"):
        response = client.post("/api/v1/cutting/files/search", json={**base, "sort": [{"field": "document_no", "direction": direction}]})
        assert response.status_code == 200
        names.append(response.json()["data"]["items"][0]["document_no"])
    assert names[0] < names[1]


@pytest.mark.parametrize("user,expected", [(None, 401), ({"role": "worker"}, 403)])
def test_condition_endpoints_require_admin(api, user, expected):
    app, client, _ = api
    app.dependency_overrides[optional_user] = lambda: user
    assert client.get("/api/v1/conditions/cutting_files/schema").status_code == expected
    for path in ["/api/v1/cutting/files/search", "/api/v1/cutting/downloads/search"]:
        assert client.post(path, json={}).status_code == expected
