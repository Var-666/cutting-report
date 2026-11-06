# API 契约、数据库迁移与车间边界

## API 契约

车间 `/api/v1/workshop`、派发 `/api/v1/cutting`、告警 `/api/v1/alarms` 的 JSON 成功响应由 Pydantic 模型声明；文件下载保持 FileResponse。响应字段在 `backend/app/models/*_contracts.py`，车间请求在 `workshop_requests.py`。缺少必需字段时响应校验失败，不返回假成功。

前端 `src/api/generated/` 是 Hey API 的生成产物，不手改。`src/types/cutting.ts`、`alarms.ts` 和车间报表类型只导出生成类型；页面草稿、浏览器请求选项保留本地类型。缓存、Cookie、设备参数和取消请求仍由现有 API 适配层负责。

使用 Node >=22.18，在前端目录运行：

```sh
pnpm run api:generate
pnpm run api:check
```

脚本使用根目录 `.venv` 中的 Python，也可通过 `PYTHON` 指定已安装后端依赖的解释器。导出路由 OpenAPI 不启动 Web/Worker、不初始化或连接数据库，也不需要运行开发服务器。`api:check` 在临时目录重新生成并比对；GitHub 的 API contracts 工作流检查生成产物是否过期。

变更顺序：修改 Pydantic/路由 → 生成 → 修正调用方 → 执行对应检查。配置、自动化、焊接、领导/设备/能耗、报表任务及 AI 等接口也已接入生成范围；具体序列化兼容规则见 [AGENTS.md](../../AGENTS.md#api-类型契约p07)。

## 状态库与迁移

正式状态库是PostgreSQL，结构位于`backend/app/database/postgres_migrations/`；只能追加编号迁移，不改已发布脚本，不在仓储、请求或普通启动时临时建表。维护工具使用`cutting_migrator`显式执行`schema`，运行账号`cutting_app`无DDL权限。

当前维护入口为`scripts/manage_postgres.py schema|status|prepare-consumers`。旧SQLite升级/导入能力已退役，不恢复原迁移目录；尚未迁移的旧部署使用保留的历史版本工具。PG账号、备份、结构维护和恢复统一见[状态库说明](../deployment/PostgreSQL状态库迁移.md)，不对生产SQL Server/MySQL执行应用结构迁移，也不删除现场旧数据库文件。

## 车间模块边界

- `api/workshop.py`：HTTP 输入、角色依赖、响应模型和错误码映射。
- `services/workshop_service.py`：查询组织、筛选/统计/分页、日报生成和确认流程；不导入 FastAPI，不直接执行 SQL。
- `database/users.py`：用户与权限身份记录；`sessions.py`：登录会话。
- `database/workshop_reports.py`：日报、任务快照；`confirmations.py`：确认、撤销/改派和审计。
- `database/workshop_scope.py`：共享设备范围约束。
- `database/workshop.py`：保留原公开导入的兼容门面，不再承载实现。新运行时和新业务使用独立仓储；既有嵌入式调用和旧测试可继续使用门面，后续迁移调用方后再删除。

与并发有关的条件更新、修订号检查、设备范围和权限冲突仍在同一数据库事务内校验；不为了分层将它们拆成先检查后写入的两个事务。UnitOfWork 显式提供日报、确认、用户和会话仓储，绑定同一连接；外部 SQL Server、模型、SMTP、魔盒调用不放进PostgreSQL状态写事务。
