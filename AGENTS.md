# 项目开发规范

## 工作范围

- 先检查 `git status` 和 `git diff`，再读 [文档入口](docs/README.md) 中与任务直接相关的章节和代码。保留已有未提交修改。
- 开发任务在当前 Git 分支上完成。仅在用户明确要求时创建新分支。
- 只修改完成需求所必需的代码。修改共享接口、认证、统计口径或后台机制前，用 `rg` 查调用方与测试。新增业务规则、入口或流程需有明确需求。
- 优先复用现有模块、组件和机制。删除兼容入口前检查源码、脚本、测试及嵌入式调用。
- 在 API、CLI 和外部 I/O 边界校验。内部依赖类型与已建立的不变量。禁止宽泛捕获异常后静默返回空值、重复判空或增加无依据的兼容兜底。Python 保留类型标注，Vue 使用 Composition API。

## 后端与数据安全

- 领域实现放在 `backend/app/modules/`，应用装配放在 `backend/app/runtime/`。依赖遵循 Routes → Service → Repository。路由负责校验、授权、容量与响应。Service 负责业务编排并显式接收依赖，独立于 HTTP 和全局状态。Repository 持有 SQL 和原子约束，依赖方向保持单向。跨模块使用公开接口。
- PostgreSQL 是唯一应用状态库。跨仓储原子写入使用 `UnitOfWork`，外部 SQL、LLM、SMTP 和文件 I/O 在状态写事务之外执行。已发布迁移只追加，不改写。普通启动不安装 Schema。
- 生产 SQL Server、MySQL、HR、PDM 和能管平台默认只读。连接通过 `DatabaseSourceRegistry` 取得。连接测试仅执行固定的 `SELECT 1`。
- 新请求和任务使用当前配置快照，在途操作保持原引用。沿用现有保存锁、原子发布与热加载白名单。配置诊断只读已发布状态。
- Web、Scheduler、Consumer、Automation 各守角色边界。保留共享容量、持久化队列、租约、提交围栏和未知外部结果的恢复保护。
- API 必须实施角色授权。设备相关请求、任务、缓存和写入显式保留设备、产线与来源身份。`X-Production-Line` 只限制能力范围。移动模块时保留已用于运维事件指纹的 logger 名称。
- 禁止提交密钥、Token、Cookie、`.env`、运行数据和原始资料。响应、日志、诊断和浏览器不得暴露凭据。禁止手工修改 `frontend/dist/`、生成的 API 类型或构建同步的 `vendor/echarts.min.js`。

## 接口、报表与前端

- HTTP 契约以 FastAPI/Pydantic 为准。保留既有字段、序列化、状态码和下载语义。修改契约时按 [API 契约说明](docs/design/API契约与迁移约定.md) 更新生成类型和调用方。
- 页面、导出、邮件和分析共享统计口径、单位、时区及区间。缺失值按业务规则保留。改变统计范围、仪表组、设备映射或告警范围前核对业务依据。
- 复用现有设计系统和公共组件。表格保持单行，长文本可查看全文，窄屏通过表内横向滚动呈现。报表草稿与已应用筛选分离。表格列宽、样式和交互细节见[前端开发](docs/project/development.md#前端开发)。
- 报表交付按[展示与核对](docs/project/功能与接口参考.md#报表展示与核对)验收。统计区间、单位、数量和状态通过标题、字段、数据或状态标签表达。

## 测试与交付

- 先根据改动和调用关系确定影响范围。功能变化补同层级回归，优先运行最小相关用例。共享能力变更覆盖实际调用方。仅在明确要求、发布门禁或局部测试不足以覆盖重大跨模块风险时执行全量测试。失败后定向修复及复验，已通过且条件未变的用例无需重复运行。
- 公共测试数据与桩放测试辅助模块，pytest 公共夹具经 `conftest.py` 注册。前端用 Vue Test Utils 验证真实组件与用户可观察的结果，纯函数直接导入。禁止以源码正则或截取执行替代行为测试。UI 改动覆盖窄屏、空数据、加载及失败状态。
- 代码交付须通过以下静态门禁。CI 的 Backend Tests、API contracts 和 Frontend CI 均为合并检查，覆盖范围见[开发指南](docs/project/development.md#测试与构建)。

```bash
.venv/bin/ruff check backend/app
pnpm --dir frontend exec pyright --project ../backend/pyproject.toml --pythonpath ../.venv/bin/python
pnpm --dir frontend run lint
pnpm --dir frontend run typecheck
```

禁止用 `# noqa`、`# type: ignore`、`# pyright: ignore`、`eslint-disable`、`@ts-ignore`、`@ts-expect-error` 或 `as any` 绕过门禁。外部 I/O 边界或已授权业务降级确有误报时，逐行豁免明确规则并注明原因。

## 按任务查阅技术文档

| 修改范围 | 查阅文档 |
| --- | --- |
| 模块依赖、Runtime、注册新产线、后台职责 | [系统架构](docs/project/architecture.md) |
| 配置来源、冷热字段、数据源接入 | [配置参考](docs/deployment/配置参考.md) |
| 持久化、事务、租约与停机迁移 | [事务设计](docs/design/PostgreSQL仓储与事务设计.md)、[进程部署](docs/deployment/WEB_WORKER进程隔离升级说明.md)、[状态库维护](docs/deployment/PostgreSQL状态库迁移.md) |
| 快照、缓存、焊接性能和刷新 | [报表缓存与快照](docs/project/multi-line-report-data.md)、[焊接接入](docs/project/welding-integration.md) |
| 业务统计、权限、派发与导出 | [功能与接口参考](docs/project/功能与接口参考.md) |

持久细节更新到对应技术文档，一处维护，多处引用。新共享文档同步检查文档索引、Git 跟踪和打包范围。
