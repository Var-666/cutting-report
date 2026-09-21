# 后端开发状态

> 更新日期：2026-09-15。

## 分层约定

- `app/api/`：FastAPI 路由、Pydantic 输入校验、权限与响应封装。
- `app/services/`：业务规则、统计、报表、外部系统协调。
- `app/database/`：SQLite repository、SQL Server/PDM 客户端和 Excel 只读适配。
- `app/workers/`：应用生命周期内的单进程后台工作线程。

## 2026-09-14 实现状态

### 车间确认更正

- 工人撤销：`POST /api/v1/workshop/tasks/{task_id}/revoke`。
- 管理员更正：`POST /api/v1/workshop/tasks/{task_id}/correct`。
- 历史日报中 `scope_machine_no` 缺失时仍可正常定位原报表并更正，不得因新字段缺失阻断旧数据。
- 确认变更继续保留审计记录，不写回生产任务表。

### 切割文件批量推送

- `POST /api/v1/cutting/dispatches/batch` 一次创建 1–20 个任务，支持直接携带 PDM 文档、版本和路径快照，也兼容历史 `download_id`；每个文件独立携带材质、厚度和数量。
- 直接派发会在建任务时复核 PDM 当前版本和路径，执行时按“下载、校验、上传、指派”推进，不要求用户预先保存文件。
- 服务返回创建、重复和失败的分项结果；前端对新创建任务顺序执行，避免同一魔盒通道并发推送。
- 活动任务的重复判断与插入在 SQLite `BEGIN IMMEDIATE` 短事务内完成；同一文件版本和机床的并发建任务只允许一个创建成功，其余返回同一条重复任务。
- 推送成功后 `FileDownloadService.delete_after_push()` 删除对应实体文件，但保留下载记录、清理原因和推送任务标识。
- 清理失败不回滚已成功的远端推送，而是以 `local_file_cleanup.error` 警告管理员。
- 文件保存仍复核当前 PDM 文档版本和 `FilePath`；同版本存在历史物理文件记录时，使用查询已稳定选出的最新记录，不再仅因记录数量大于 1 拒绝重复保存。

### 智能分析缓存

- 领导、车间和设备报表接口返回 `analysis_source_version`，用于前端识别业务数据变化。
- `ReportAnalysisService` 对完整模型上下文进行稳定序列化和 SHA-256 计算，生成 `data_version`。
- SQLite `report_analyses` 按报表类型、日期和数据版本核对缓存；旧表自动补充 `data_version`。
- 同一进程内，同一版本的并发请求只调用一次模型；手动“重新分析”会替换该版本的缓存。

### 报表自动化参数

- `GET /api/v1/automation/configuration` 返回自动化、扫描、重试和 SMTP 配置，只返回密码是否已配置。
- `PUT /api/v1/automation/configuration` 验证端口、超时、发件人及 TLS/SSL 互斥规则，原子写入 `data/settings.env`。
- 密码留空会保留运行配置文件中的最新值；保存其他自动化参数不覆盖数据库配置。
- 参数只在重启服务后应用，不对当前 service/worker 实例做半热更新。

## 验证要点

- 涉及任务确认时覆盖旧日报、重复确认、撤销/改派和审计。
- 涉及文件推送时覆盖批量上限、重复任务、顺序执行、完整性校验与成功后清理。
- 涉及分析时覆盖数据版本稳定性、业务数据变化、强制刷新和并发合并。
- 涉及运行配置时覆盖密码不泄漏、空密码保留、其他配置保留、文件权限和输入校验。
