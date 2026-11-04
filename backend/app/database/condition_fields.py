"""Server-owned query fields and ordering expressions for PDM and downloads."""
from app.models.conditions import ConditionError, FieldSpec


def fields_for(dataset, settings=None):
    if dataset == "cutting_files":
        sent = settings.file_status_sent_value if settings else "已派发"
        failed = settings.file_status_failed_value if settings else "派发失败"
        return (
            FieldSpec("project_id", "项目ID（ProjectInfoID）", "text", "a.ProjectInfoID", hint="对应原SQL的 a.ProjectInfoID，适合精确匹配项目GUID。"),
            FieldSpec("document_id", "设计文档ID（ID）", "text", "a.ID"),
            FieldSpec("catalog_id", "文件分类 / 分组ID（FileID）", "text", "a.FileID"),
            FieldSpec("catalog_title", "文件分类 / 分组名称（Title）", "text", "c.Title"),
            FieldSpec("catalog_parent_id", "上级目录ID（ParentID）", "text", "c.ParentID"),
            FieldSpec("catalog_type", "目录类型（Type）", "number", "TRY_CONVERT(decimal(18,4), c.Type)", hint="1 表示分类，2 表示分组。"),
            FieldSpec("classification_type", "分类类型（ClassificationType）", "text", "c.ClassificationType"),
            FieldSpec("document_no", "文件名（DocumentNo）", "text", "a.DocumentNo"),
            FieldSpec("document_cname", "文档中文名（DocumentCName）", "text", "a.DocumentCName"),
            FieldSpec("document_ename", "文档英文名（DocumentEName）", "text", "a.DocumentEName"),
            FieldSpec("pbs_info", "中间产品 / 分段（PBSInfo）", "text", "a.PBSInfo"),
            FieldSpec("version_id", "版本ID（VersionID）", "text", "a.VersionID"),
            FieldSpec("version", "版本（Version）", "text", "v.Version"),
            FieldSpec("document_state", "文档状态（DocumentState）", "text", "a.DocumentState"),
            FieldSpec("operator", "文档操作人（Operator）", "text", "a.Operator"),
            FieldSpec("operator_job_no", "文档操作人工号（OperatorJobNo）", "text", "a.OperatorJobNo"),
            FieldSpec("operate_date", "操作日期（OperateTime）", "date", "TRY_CONVERT(date, a.OperateTime)", "operate_time"),
            FieldSpec("operation_type", "操作类型（Custom1）", "text", "a.Custom1", "operation_type"),
            FieldSpec("downloaded_by", "PDM下载人（Custom2）", "text", "a.Custom2", "downloaded_by"),
            FieldSpec("downloaded_by_job_no", "PDM下载人工号（Custom3）", "text", "a.Custom3", "downloaded_by_job_no"),
            FieldSpec("downloaded_date", "PDM下载日期（Custom4）", "date", "TRY_CONVERT(date, a.Custom4)", "downloaded_at"),
            FieldSpec("downloaded_version", "PDM下载版本（Custom5）", "text", "a.Custom5", "downloaded_version"),
            FieldSpec("dispatch_state", "派发状态（Custom6）", "enum", "a.Custom6", "custom6",
                      ((sent, "已派发"), (failed, "派发失败")), "选择“为空”表示待派发；未识别状态不会自动归为已派发。"),
            FieldSpec("physical_file_id", "物理文件ID（ID）", "text", "physical.physical_file_id"),
            FieldSpec("physical_file_name", "物理文件名（FileName）", "text", "physical.FileName"),
            FieldSpec("physical_file_type", "物理文件类型（FileType）", "text", "physical.FileType"),
            FieldSpec("physical_version_no", "物理版本序号（VersionNo）", "number", "TRY_CONVERT(decimal(18,4), physical.VersionNo)"),
            FieldSpec("physical_version_code", "物理版本代码（VersionCode）", "text", "physical.VersionCode"),
            FieldSpec("physical_file_date", "物理文件日期（FileDate）", "date", "TRY_CONVERT(date, physical.FileDate)", "physical_file_date"),
            FieldSpec("physical_file_operator", "物理文件操作人（FileOperator）", "text", "physical.FileOperator"),
            FieldSpec("physical_file_operator_job_no", "物理文件操作人工号（FileOperatorJobNo）", "text", "physical.FileOperatorJobNo"),
            FieldSpec("physical_page_size", "页面尺寸（PageSize）", "text", "physical.PageSize"),
            FieldSpec("file_path", "文件相对路径（FilePath）", "text", "physical.FilePath"),
        )
    if dataset == "cutting_downloads":
        return (
            FieldSpec("project_id", "项目ID", "text", "(payload::json->>'project_id')"),
            FieldSpec("catalog_id", "目录ID", "text", "(payload::json->>'catalog_id')"),
            FieldSpec("filename", "系统文件名", "text", "(payload::json->>'filename')"),
            FieldSpec("document_no", "文档名称", "text", "(payload::json->>'document_no')"),
            FieldSpec("version", "版本", "text", "(payload::json->>'version')"),
            FieldSpec("relative_path", "来源相对路径", "text", "(payload::json->>'relative_path')"),
            FieldSpec("created_by_name", "下载操作人", "text", "(payload::json->>'created_by_name')"),
            FieldSpec("download_date", "下载日期", "date", "substr(created_at, 1, 10)", "created_at"),
            FieldSpec("size", "文件大小（字节）", "number", "(payload::json->>'size')::numeric"),
            FieldSpec("status", "下载记录状态", "enum", "status", options=(("downloading", "下载中"), ("downloaded", "已下载"), ("failed", "下载失败"))),
            FieldSpec("source_mode", "文件来源", "enum", "(payload::json->>'source_mode')", options=(("http", "FO HTTP 文件服务"), ("local", "本地目录"), ("mock", "模拟来源"))),
        )
    raise ConditionError("不支持的查询对象")


def sort_fields_for(dataset, settings=None):
    fields = fields_for(dataset, settings)
    if dataset == "cutting_files":
        allowed = {"catalog_title", "document_no", "project_id", "pbs_info", "version",
                   "operator", "operate_date", "physical_version_no", "physical_file_date"}
        return tuple(field for field in fields if field.key in allowed)
    return fields


def sort_expression(field):
    # Date filters use a whole day, but sorting retains the full timestamp.
    return {"operate_date": "a.OperateTime", "physical_file_date": "physical.FileDate",
            "download_date": "created_at"}.get(field.key, field.expression)
