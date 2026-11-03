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

## SQLite 版本迁移

`backend/app/database/migrations/registry.py` 按编号注册迁移。001–013 接管既有仓储的建表、补字段和历史补齐逻辑，保留表名与业务含义；后续结构变更新增编号文件，禁止继续在仓储 `initialize()` 中加入 ALTER TABLE，也不要修改已经发布的迁移。

启动顺序：保留既有进程升级备份、设备归属迁移 → 执行待运行的编号迁移 → 初始化内置模板、设备同步游标等运行数据。历史 `device-isolation-v2` 标记继续保留，不根据当前默认设备猜测历史记录归属。

统一 runner 使用文件锁串行执行，一批结构升级及迁移标记在同一事务提交。执行脚本不会调用会隐式提交的 `sqlite3.executescript`；失败整体回滚，并检查外键完整性。已执行的编号不重复运行。已有数据库执行待运行批次前，通过 SQLite Backup API 备份已提交数据（含 WAL）到 `data/backups/*-before-schema-*.db`，备份文件权限 600；备份失败即停止。

手工查看与升级（路径必须明确指定）：

```sh
./.venv/bin/python scripts/migrate_database.py status --database /absolute/path/data/metadata.db
./.venv/bin/python scripts/migrate_database.py upgrade --database /absolute/path/data/metadata.db
```

status 只读，不创建数据库。手工 upgrade 前停止 Web 和 Worker；命令拒绝活动 Web 进程锁或尚未释放的 Worker 租约。升级前保留原镜像、配置、完整 data 和 reports 卷。

### 失败和恢复

- 单批编号迁移失败：事务回滚；保留错误与备份，修复迁移原因后重试，不手动清除标记。
- 旧版设备迁移有自己的事务与备份；多个历史阶段之间不承诺一个总事务，具体恢复沿用设备隔离文档。
- 已升级并接收新业务：不要直接恢复旧备份，否则会丢失后续确认、派发和审计。先保全当前库，确定向前修复或增量恢复方案。
- 确认没有新增业务、需要恢复时：停止全部进程，先另备份当前库；使用 SQLite Backup API 将备份恢复到目标库，再启动与备份结构匹配的旧版本。不要在线只覆盖主 `.db` 文件并保留旧 WAL/SHM。
- 不在生产 SQL Server、PDM、HR 或焊接 MySQL 上执行这些迁移。

## 车间模块边界

- `api/workshop.py`：HTTP 输入、角色依赖、响应模型和错误码映射。
- `services/workshop_service.py`：查询组织、筛选/统计/分页、日报生成和确认流程；不导入 FastAPI，不直接执行 SQL。
- `database/users.py`：用户与权限身份记录；`sessions.py`：登录会话。
- `database/workshop_reports.py`：日报、任务快照；`confirmations.py`：确认、撤销/改派和审计。
- `database/workshop_scope.py`：共享设备范围约束。
- `database/workshop.py`：保留原公开导入的兼容门面，不再承载实现。新运行时和新业务使用独立仓储；既有嵌入式调用和旧测试可继续使用门面，后续迁移调用方后再删除。

与并发有关的条件更新、修订号检查、设备范围和权限冲突仍在同一数据库事务内校验；不为了分层将它们拆成先检查后写入的两个事务。UnitOfWork 显式提供日报、确认、用户和会话仓储，绑定同一连接；外部 SQL Server、模型、SMTP、魔盒调用不放进 SQLite 长事务。
