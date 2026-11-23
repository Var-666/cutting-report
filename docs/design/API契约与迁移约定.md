# API 契约、数据库迁移与车间边界

## API 契约

FastAPI/Pydantic 是 HTTP 数据契约的来源。业务模型在所属模块的 `contracts.py` 或共享 `backend/app/models/`。新增 JSON 接口优先使用明确的请求模型和 `ApiEnvelope[T]`，分页复用 `Items/Page` 并如实描述部署中的响应。焊接分页字段在 `query` 下。动态 SQL 列、能耗诊断等确实动态的字段可保留 JSON 类型，稳定响应使用具体模型。

补齐旧接口类型时保留数字及 Decimal、日期、空值、缺失字段、附加字段和各状态分支。配置、自动化、焊接、领导、设备、能耗、报表任务及 AI 等既有接口使用 `responses={状态码: {'model': ApiEnvelope[T]}}` 配合 `response_model=None` 描述契约。该写法沿用现行序列化，不引入响应过滤和额外查询。修改契约或给新接口启用运行时响应校验时，用实际 HTTP 或 Service 载荷验证。下载按实际 MIME 描述并沿用文件响应。

前端 `src/api/generated/` 由现有 `@hey-api/openapi-ts` TypeScript 插件生成。业务调用优先引用生成的数据类型，稳定的已有名称可保留为类型别名。只写请求字段使用对应的 `*Writable` 类型。`request<T>()` 已解包 `data`，因此 `T` 使用业务数据类型。Cookie、产线和设备、取消、重试、缓存及下载继续由现有 API 适配层负责。

页面草稿、URL 字符串筛选、驼峰参数适配、图表补零与任务轮询展示状态由前端处理，优先从生成类型派生。创建报表的最小接受响应和完整任务详情使用各自的类型。字段不一致时修正契约或调用方。

### 生成类型

新增参与类型复用的 Router 同步登记到 `scripts/export_api_contracts.py`。脚本只挂载 Router，导出时无需启动 Runtime 或连接数据源。在 `frontend/` 依次执行：

```sh
pnpm run api:generate
pnpm run api:check
pnpm run typecheck
```

使用 [开发指南](../project/development.md#环境准备) 的 Node 版本及 `.venv`，可通过 `PYTHON` 指定已安装后端依赖的解释器。`api:check` 在临时目录重新生成并比对。更新生成类型后修正调用方，并定向验证受影响的响应与调用。认证、设计器和系统诊断的历史接口按实际改动逐步纳入。

### 契约检查

[API contracts 工作流](../../.github/workflows/api-contracts.yml)启动隔离 PostgreSQL 18，复用测试账号和迁移夹具，设置 `P5_TEST_POSTGRES=1`。JUnit 门禁要求工作流列出的契约、配置及 Runtime 边界测试模块均有收集，跳过、失败和错误均为零。工作流还单独检查生成类型漂移。

## 状态库与迁移

正式状态库是PostgreSQL，结构位于`backend/app/database/postgres_migrations/`；只能追加编号迁移，不改已发布脚本，不在仓储、请求或普通启动时临时建表。维护工具使用`cutting_migrator`显式执行`schema`，运行账号`cutting_app`无DDL权限。

当前维护入口为`scripts/manage_postgres.py schema|status|prepare-consumers`。旧SQLite升级/导入能力已退役，不恢复原迁移目录；尚未迁移的旧部署使用保留的历史版本工具。PG账号、备份、结构维护和恢复统一见[状态库说明](../deployment/PostgreSQL状态库迁移.md)，不对生产SQL Server/MySQL执行应用结构迁移，也不删除现场旧数据库文件。

## 车间模块边界

- `modules/cutting/workshop_routes.py` 负责 HTTP 输入、角色依赖、响应与错误映射。
- `modules/cutting/workshop.py` 组织查询、筛选、统计、分页及日报确认流程。
- `database/users.py` 与 `database/sessions.py` 分别维护用户和会话。
- `modules/cutting/workshop_repository.py` 维护日报及任务快照，`confirmations.py` 维护确认、撤销、改派及审计。`workshop_scope.py` 承载共享设备范围约束。

与并发有关的条件更新、修订号检查、设备范围和权限冲突仍在同一数据库事务内校验；不为了分层将它们拆成先检查后写入的两个事务。UnitOfWork 显式提供日报、确认、用户和会话仓储，绑定同一连接；外部 SQL Server、模型、SMTP、魔盒调用不放进PostgreSQL状态写事务。
