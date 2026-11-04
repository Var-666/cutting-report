# 项目开发指南

## 项目用途

本项目是面向内网的切割生产智能产报系统，提供领导、车间和设备报表，工人任务确认，智能分析与告警，报表导出与自动发送，以及切割指令文件管理。

## 技术栈

- 前端：Vue 3、Vue Router、Vite、Soybean Admin、Naive UI、UnoCSS、ECharts。
- 后端：Python、FastAPI、Pydantic、Uvicorn、Jinja2。
- 数据：SQL Server（切割/HR/PDM）、MySQL（焊接）作为外部数据源，PostgreSQL 保存系统元数据、快照和操作记录；SQLite 仅用于离线历史准备/导入。
- 报表：单文件 HTML、ECharts、Playwright/Chromium PDF 转换，可选 OpenAI-Compatible LLM 分析。
- 测试：Pytest 和 Node.js 内置测试运行器。

## 核心目录

- `backend/app/api/`：HTTP 路由、请求校验和权限边界。
- `backend/app/services/`：业务流程、统计、外部集成和报表生成。
- `backend/app/database/`：SQL Server、MySQL 和 PostgreSQL 客户端、仓储与事务管理。
- `backend/app/workers/`：报表、告警、缓存刷新和清理后台任务。
- `backend/tests/`：后端回归测试。
- `frontend/src/views/`：页面；`components/` 为可复用组件；`layouts/` 为角色/功能布局。
- `frontend/src/api/`：前端 API、缓存、日期和导出边界。
- `frontend/tests/`：路由、页面契约、缓存和导出测试。
- `docs/`：需求、设计、部署、数据库和外部接口资料。
- `data/`、`reports/`、`logs/`、`tmp/`：运行时数据，不属于源码。

## 主要架构约定

- API 层保持精简；业务规则放在 service，持久化放在 repository/database 层。
- 生产 SQL Server、HR 库、PDM 库和能管平台均按外部系统处理。除非需求和设计明确授权，不对其执行写入。
- PostgreSQL 是唯一应用自有状态库；多仓储原子操作遵循 `docs/design/PostgreSQL仓储与事务设计.md`。
- 生产式部署保留一个 Web（`app.main:app`）与一个独立 Worker（`python -m app.worker`）。Web 同源提供前端和 API，不启动后台消费者；不得用多个 Uvicorn workers 替代独立 Worker。
- 管理员与工人路由、菜单和后端 API 权限必须一致；不能仅依赖前端隐藏实现授权。
- 页面查询、HTML/PDF 导出和智能分析应共用同一统计口径、时区和区间定义。
- 管理页保存的运行参数写入 `data/settings.env`，密码不回显；数据库页连接配置及自动化/SMTP 参数按新操作热加载；SQL/HTTP 容量（含单源 sql_max_concurrent）及其余启动参数修改后重启生效。

## 后端模块边界（P01）

新功能先用 `rg` 查找同类入口、Service、Repository 和测试，优先扩展已有业务模块。当前结构和待处理问题见 [后端架构说明](docs/project/architecture.md#p01-后端边界与后续治理2026-11-02)；以下约束适用于新增和修改代码，不要求顺带搬迁历史实现。

### 职责与依赖

| 位置 | 放什么 | 依赖与限制 |
| --- | --- | --- |
| `api/` | HTTP 参数/Pydantic 校验、身份与角色权限、请求容量、响应及异常状态映射 | 调用 Service；不新增业务 SQL、统计计算、跨仓储流程或配置发布流程。现有简单仓储调用可保留，复用仓储异常作 HTTP 映射不算越层业务实现 |
| `services/` | 业务规则、统计、报表流程、外部 HTTP/文件/SMTP/LLM 协调 | 依赖 `database/`、`models/` 和显式传入的配置/协作服务；业务 Service 不依赖 FastAPI、`api/` 或入口模块，不接收 `Request`/`app.state` 作为业务上下文 |
| `database/` | 数据库客户端、参数化 SQL、Repository、连接、事务和迁移 | 依赖模型、配置及基础机制；不调用业务 Service、HTTP 路由或后台调度。确认归属、唯一性、并发冲突等必须原子判断的约束留在仓储事务内 |
| `workers/`、`worker.py` | 调度、任务领取、租约、退避、容量、停止及业务执行入口 | 调用 Service；队列状态可直接使用协调仓储，不复制报表统计和业务流程，不依赖 HTTP 层 |
| `models/` | 已有领域数据、请求/响应契约和共享值类型 | 不依赖 API、Service、Worker 或 Repository；不为分层强行复制同一数据结构 |
| `core/` | 已复用的取消注册表、执行期限和进程锁 | 实现统一位于 `request_cancellation.py`、`report_deadline.py`、`process_lock.py`；不依赖 API、Service 或数据库，不放业务字段注册、统计口径或通用 Service 基类 |

- 默认业务调用方向为 `api/workers → services → database`，模型和配置供各层使用。`main.py`、`worker.py`、`services/application_runtime.py`、`services/device_runtime.py` 是入口/装配边界，允许同时依赖 Service、Repository 和 Worker；不能因这类导入机械判定为反向依赖。
- 现有 `services/frontend_static.py` 是供 Web 入口使用的 HTTP 静态资源适配，不属于业务 Service；保留位置，不向其中增加业务流程，也不由业务 Service 引用它。
- 请求取消、执行期限和进程锁以 `app.core.request_cancellation`、`app.core.report_deadline`、`app.core.process_lock` 为唯一正式入口；API、Service、Repository、Worker 及脚本均直接导入，新模块同样遵守。不在 `services/` 创建同名转发模块，不复制已有注册表、ContextVar、文件锁或执行期限实现。PostgreSQL 执行租约和提交围栏统一位于 `database/postgres.py`。
- 条件查询契约、字段描述和通用值校验在 `models/conditions.py`；数据集字段/排序列映射在 `database/condition_fields.py`，SQL 编译和 Mock 求值在 `database/condition_query.py`。`services/condition_service.py` 仅提供公开 schema，基础函数直接从所属模型/数据库模块导入，Repository 不反向引用该 Service。小型响应封装不必为消除重复而建立公共框架。
- 告警事件归并、发送认领、状态转换、唯一性和租约判断必须在原事务内完成；扫描窗口、调度日期、模型/文件生成、SMTP 和重试策略由现有 Service 编排。不能为缩短 Repository 把原子校验移出事务。

### Runtime、配置与事务

- `application_runtime.build_runtime(settings, role=...)` 统一装配进程共享资源，`build_device_runtime` 装配切割业务实现，所有实例统一登记在 `DatabaseServices.lines`。Runtime 负责对象连接与生命周期，不承载新增业务规则；业务方法接收所需服务/仓储，不自行查全局 Runtime 或重新构建它。
- Web 只在 `app.state.runtime` 挂载类型化 `ApplicationRuntime`；API 通过 `application_state(request)` 获取进程共享资源，通过 `runtime_state(request)`/`Depends(runtime_state)` 获取 `DatabaseServices` 请求快照。旧式平铺 state 仅在公共入口兼容，不在新路由继续散落属性探测。ApplicationRuntime 不保留初始产线服务字段，业务一律从当前 DatabaseServices.lines 访问。
- Web 通过 `api/runtime.runtime_state` 固定单请求数据库服务快照，通过 `device_state` 选择切割设备；Worker 通过 `database_context` 在新操作边界选择快照。在途操作保持原引用，不混用新旧服务或直接改写已发布对象。
- `config.py` 保留不可变 `Settings`、字段元数据、校验、连接配置及设备配置派生；环境解析复用 `configuration_sources`。业务代码使用传入的 Settings，不能另读环境或另写默认值。启动全局 `settings` 不代表当前热加载状态。
- 热加载复用 `DatabaseRuntime`、`AutomationRuntime`；保存复用 `runtime_settings_store` 和进程锁，保持“锁内读改写/保留空密码 → 校验并准备候选 → 原子写文件 → 发布快照”。读取或构建失败保留旧快照，诊断用 `inspect()`，不因读取诊断触发热加载。不自行扩大热加载字段范围。
- 配置来源由 `configuration_sources.load_environment` 统一解析：`data/settings.env > 进程环境变量 > .env > Settings 默认值`；`.env` 保留已有变量插值与 `PYTHON_DOTENV_DISABLED` 兼容，管理页文件按字面值读取，不展开密码中的 `${...}`。产线只取 `PRODUCTION_LINES_JSON`，连接只取 `DATABASE_SOURCES_JSON`；旧设备目录及连接环境键仅由独立 `scripts/migrate_production_lines.py` 升级工具读取，正常运行不回退。
- 数据库配置请求/字段转换及脱敏响应在 `services/database_configuration.py`，自动化配置契约及脱敏响应在 `automation_configuration.py`；保存分别调用 `DatabaseRuntime.save_configuration`、`AutomationRuntime.save_configuration`。Runtime 持有保存路径和当前配置，API 不再读取启动全局 Settings 保留密码，也不自行加锁、合并或发布。空密码优先保留锁内读取的最新文件值，文件未提供有效值时保留当前快照；不回显密码。
- 热加载白名单以 `database_runtime.DATABASE_SETTINGS` 和 `automation_runtime.HOT_SETTINGS` 为准：数据库页已有连接/启用字段（含焊接时区）及自动化/SMTP 字段可热加载；产线目录和数据源连接选项（含单源超时/池大小）已获 P1 授权热加载，P3 容量字段 sql_max_concurrent 除外；P2 将 `REPORT_BACKGROUND_REFRESH_SECONDS` 纳入同一快照，保持原最小30秒。AI、报表 TTL 等其他启动参数仍需重启。新字段先在 `Settings` 登记环境键和默认值，只有明确授权热加载才补入白名单及相应管理契约，不能因字段以数据库前缀开头就自动热加载。
- 诊断继续通过 `ConfigurationDiagnostics` 对比各进程 `inspect()` 已发布值与文件目标，展示实际来源和重启要求；不能调用 `snapshot()` 使诊断变成配置发布。Worker 按独立心跳报告其快照，未加载/不可用不能标为已生效；敏感值及内部比较指纹不出现在诊断 API 中。
- 状态业务连接统一经 `PostgresDatabase`/`PostgresRepository`；跨仓储原子写入使用 `UnitOfWork` 绑定同一连接并由外层提交。Service 不持有原始连接、不自行提交；单仓储原子校验/写入由仓储完成。PG编号迁移复用 `database/postgres_migrations/`；旧 `database/migrations/` 只供离线历史准备，不在路由或后台任务临时建表。
- SQL/LLM/SMTP/文件等耗时外部操作不得放进状态库写事务；保留现有任务状态、幂等、未知结果保护和失败补偿。不要用事务回滚假定外部副作用已撤销，详见 [PostgreSQL 事务设计](docs/design/PostgreSQL仓储与事务设计.md)。
- 两进程通过同一 PostgreSQL 状态库与同机共享文件卷协作，各复用一个进程连接池；保留 Web OS 锁、Worker 租约/提交围栏、持久化队列及现有共享容量，不依赖跨进程内存对象或为每台设备新增常驻线程。不扩展为多主机或网络共享卷部署。
- SQL 资源统一通过 `core/sql_capacity.py` 在 SQL Server/MySQL 驱动入口获取全局、数据源、产线配额；同机 Web/Worker 共用固定文件锁槽。等待不得持有部分执行配额，不向状态库写配额状态。容量及单源 `sql_max_concurrent` 为冷配置，连接参数仍按新操作热加载；资源计数为各进程本地观测，不能声称实时全局统计。HTTP 执行池与 SQL、LLM、Chromium 容量分别管理。
- Worker 调度沿用 `WorkerProcess` 的有界 monotonic 提示状态；PostgreSQL `background_schedules/background_tasks` 仍决定真实到期、去重和重试。产线通过 `BackgroundSchedule` 提交周期/每日计划，耗时准备只在到期时执行；失败或未确认持久化不得推进内存进度。新增/停用/来源/间隔/计划变化随原快照处理，移除失效状态；队列保持每秒领取、5秒心跳，延迟接纳常规5秒及容量释放时检查，保留 `next_attempt` 与原容量。

### 多产线与复用

- 唯一实例目录由 `device_config.ProductionLineCatalog` 从 `PRODUCTION_LINES_JSON` 校验，`settings_for_line` 派生业务配置，类型能力复用 `production_lines.py`。新增同类型实例只改配置并验证绑定与隔离，不改公共 Runtime/Worker/API；业务 SQL 仍放对应 `database/queries/` 或仓储。
- 复用现有 `ProductionReportProvider`、`QueryCache`、`CacheRevision` 和报表快照能力；Provider 只统一访问/刷新契约，不强行统一切割与焊接的指标、日期、单位和持久化结构。焊接保留专属只读 MySQL 仓储及 Service；切割和焊接均由 `lines[line_id]` 管理，不新增全局单例字段。
- Provider 提供 `report_types/source_identity/fetch_summary/fetch_detail/refresh_summary/refresh_plan`；查询类型和明细结果由产线保留，后台查询参数须能 JSON 序列化。跨服务调用公开方法，不能访问其他 Service 的私有方法。`requested_at` 是 Worker 任务提交时间，沿用现有刷新合并语义。
- 新增产线类型登记构建函数和能力，业务实例只登记在统一目录，实现专属 Repository、Service/Provider，并在类型注册的构建函数与 `ProductionLineRuntime` 可选能力中接入；按需增加 API/页面和隔离测试。已有 Worker 按统一实例集合消费 `refresh_plan` 和 `report_snapshot_refresh`，不要为每种产线增加调度分支或缓存框架。切割实例仍使用统一目录内的物理映射，切割月报保留原 bootstrap/AI 调度。
- 普通查询缓存用有界 `QueryCache`，缓存键或服务实例必须隔离产线/设备、来源、规范化条件及必要版本；可变返回值由调用方复制。领导月报直查和焊接短期统计复用该缓存；领导月快照的新鲜度缓存仍保留专属规则，焊接 AI 结果缓存仍按业务版本独立保存。
- 通用持久化快照复用 `ReportSnapshotService/ReportKey`、`CacheRevision` 和原持久化队列，键包含产线、类型、日期、参与快照计算的筛选及必要分页/排序、来源、schema 及权限范围；完整聚合视图的分页投影例外见 P09。保留焊接页面快照优先、冷页异步准备、过期标识、失败保留与源信息采样；导出/下钻保持现场查询，不用旧快照绕过实时校验，不强制各业务使用相同 TTL。
- 区分来源、失效修订、存储校验和与业务版本：`source_identity` 标识来源隔离；`CacheRevision` 仅失效进程缓存；快照存储的 `data_version` 校验完整载荷。公开页面/AI 业务版本继续由现有算法生成：焊接页面及导出共用 `WeldingService.version`，切割沿用现有分析版本；不能用存储校验和替换。焊接 API 的 `meta.report_data.data_version` 与公开业务版本一致；AI 校验页面版本，现场导出随源数据变化可产生新版本，不能覆盖页面快照。
- 设备相关请求、任务、缓存与持久化操作显式保留设备/产线及来源身份，切割请求使用唯一有效的 `device_id`，不回退默认设备。`X-Production-Line` 仅限制能力范围，不能代替角色权限或授予产线权限。
- `WorkshopRepository` 是兼容门面；新代码使用用户、会话、日报、确认专属仓储。现有线程模式、旧调用签名和配置兼容分支先查实际调用方及测试，后续专项处理，不在新功能中扩散或顺手删除。

## 编码和验证规范

- 每次任务先检查 `git status`/`git diff`，再读取本次直接相关的文档和代码。
- 优先最小范围修改；不重构、格式化或修复任务外代码。
- 只实现用户明确说明的功能；不得基于未经确认的业务假设自行增加页面入口、操作流程或管理功能。未明确说明的新增需求或业务规则，先澄清再实现。
- 保留现有风格：Python 使用类型标注和 Pydantic 边界校验；Vue 使用 Composition API；JavaScript 不强制分号。
- 公共 API、数据结构、认证、统计口径或背景工作机制改动前，先用 `rg` 检查调用方和对应测试。
- 后端验证：`./.venv/bin/python -m pytest backend/tests`。
- 前端验证：在 `frontend/` 执行 `pnpm run typecheck` 和受影响的局部测试；`pnpm test` / `pnpm run build` 用于发布、CI 或明确需要的全量验证。
- 功能修改应补充相同层级的回归测试；涉及 UI 时同时检查窄屏和空数据/加载/失败状态。
- 测试优先验证用户行为、渲染状态和公开契约，不用源码正则锁定内部函数名、变量名、组件布局或 CSS 写法。前端组件测试可复用 `tests/vue-component-helpers.mjs` 编译真实 SFC；已有有效的源码提取行为测试逐步迁移，稳定依赖/权限边界检查可保留。
- 复用已有公共能力，不新增无明确职责的兼容转发模块；少量重复不必建立新抽象。删除兼容入口前检查源码、脚本、测试及嵌入式调用，不因层次多而合并 Runtime、缓存或快照机制。

## API 类型契约（P07）

- FastAPI/Pydantic 是 HTTP 数据契约的来源。新增 JSON 接口优先使用明确的请求模型及 `ApiEnvelope[T]` 响应模型；复用 `models/contracts.py` 的 `Items/Page` 和已有领域/业务契约。分页只描述实际返回的字段：焊接分页位于 `query` 中，不能凭空在外层补 `page/page_size`。动态 SQL 列、能耗诊断细节等保留 JSON 动态字段，不用 `dict/any` 代替整个稳定响应。
- 旧接口补类型必须保持原返回值，尤其是数字/Decimal、日期、空值、缺失字段、附加字段和状态分支。当前配置、自动化、焊接、领导/设备/能耗、报表任务和 AI 接口用 `responses={状态码: {'model': ApiEnvelope[T]}}` 配合 `response_model=None` 描述契约，保留既有序列化，不增加响应过滤或查询。修改这些契约时用实际 HTTP/Service 载荷做定向兼容验证；新接口使用运行时响应校验前同样核对序列化。下载继续返回原文件响应，按实际 MIME 描述，不包 JSON。
- `scripts/export_api_contracts.py` 挂载契约范围内的 Router，不启动 Runtime、不连接数据源。前端只通过 `pnpm run api:generate` 调用现有 `@hey-api/openapi-ts` TypeScript 插件更新 `src/api/generated/`，不手改生成文件、不生成第二套 HTTP SDK。新增纳入类型复用的 Router 时同步补入导出范围。
- 前端业务 API 优先引用生成的请求与响应数据类型，已有共享名称可用类型别名兼容；密码等只写请求字段使用生成器的 `*Writable` 类型。`request<T>()` 已解包 `data`，`T` 应是业务数据，不能再次使用完整 envelope，也不能为生成器修改 Cookie、设备/产线、取消、重试、缓存和下载机制。
- 页面草稿、URL 字符串筛选、驼峰参数适配、图表补零和任务接受到轮询详情之间的展示状态由前端维护，优先从生成类型派生。API 字段/类型不一致应修正契约或调用方，不用 `any`、无依据的断言或类型抑制掩盖；报表创建的最小接受响应不能当作完整任务详情。
- 契约修改后执行 `pnpm run api:generate`、`pnpm run api:check`、`pnpm run typecheck` 及受影响的定向测试。现有 API contracts CI 检查本地响应契约和生成漂移，Frontend CI 检查 TypeScript；保持精简，不因此运行全量后端回归。认证、设计器和系统诊断等未迁移模块按后续实际修改逐步覆盖，不为全覆盖重写历史业务。

## 焊接查询性能（P09）

- 日报与设备报表复用规范化的 `page:daily` 快照：其中已包含有界的完整按日、设备/机器人聚合数组，响应按当前分页/排序投影；查询条件、日期、来源及权限范围继续隔离。投影必须保留原 `data_as_of/refreshed_at/expires_at`、过期与刷新状态，不得把旧聚合标为刚查询。不得将此模式套用于未完整保存明细的任务/工件页。
- 任务/工件页继续按分页/排序隔离，统计、总数及当前页明细保持同一只读 MySQL 事务；总数复用相同谓词的统计结果，不能为减少查询而混合旧快照统计与实时明细。源信息继续按原间隔采样；导出和下钻保留现场查询，不增加连接池、消费者或第二套缓存。
- `refresh_plan` 先预热首页，再预热日报公共聚合、任务及工件，共四个任务；设备页复用日报，不另排周期刷新。请求与 Worker 继续共用原队列去重、产线锁和失败恢复；不同分页/排序的聚合视图刷新应合并为同一任务。
- 已配置快照且有后台刷新协调器的页面读与分析前置读使用独立、有界的 `snapshot_requests`，避免排在慢导出/下钻后；实际 MySQL 操作及缺少快照或协调器的嵌入式回退继续使用 `welding_requests` 的原并发上限。区分准备中、队列满、执行池排队、连接池耗尽和源查询失败，不扩大自动重试范围。
- 前端继续使用 `reportCache`，缓存键规范化参数顺序和查询默认值；实际参数序列化、取消、代际与已提交筛选保持原规则。首页只监听有效产线/规范化查询变化，无关 URL 参数不应清空数据、重发请求或重启轮询。日志只记录产线、类型、日期、命中与分阶段耗时，不记录凭据、完整筛选或 SQL 参数；现场索引/SQL 扫描结论须有真实表结构、规模和 EXPLAIN 证据。

## 前端设计开发规范

- 沿用 Soybean Admin + Naive UI + UnoCSS；新增或修改页面前，先检查 `frontend/src/theme/`、`assets/style.css`、`uno.config.ts` 和已有公共组件，不另建独立设计系统。
- 颜色、背景、文字、边框和阴影使用语义化主题变量；Naive UI 变量由 `theme/reportVariables.ts` 桥接，避免重复硬编码主题色，并检查亮色、暗色及主题色切换。
- 优先使用现有字号变量及 4/8/12/16/24px 间距：辅助文字和控件 12px、正文 13px、区块标题 16px；首页指标保留 30px 变体。报表筛选控件默认 40px 高，控件圆角跟随 Naive 主题，卡片 8px、报表面板 10px；图表尺寸、紧凑表格和特殊业务布局允许明确的局部例外。
- 表格、筛选栏、指标卡和图表面板优先复用 `AppReportTable`、`StandardReportQuery`、`ReportPeriodPicker`、`HomeKpiCard`、`ReportTrendPanel`，先完善现有组件，避免重复实现。
- 焊接首页与报表的指标带统一通过 `WeldingKpiCards` 组合 `HomeKpiCard`；首页数值沿用 30px，报表使用现有 24px 指标变量，指标颜色按业务含义保持一致。复杂焊接筛选保留专属表单，与标题组成同一查询区域；布局、筛选展开和主题切换不得改写已提交条件或额外触发查询。
- 页面专属样式放在对应组件的 scoped 样式中；全局 CSS 只维护主题别名、基础规则和跨页面公共规范，不持续追加业务选择器。
- 优先通过组件 props、`theme-overrides` 和局部 CSS 变量调整 Naive 控件；不随意添加 `!important`，确需覆盖库内联变量或已有兼容规则时说明原因并限定范围。
- 切割线、焊接线沿用统一视觉语言，保留业务布局差异；设计整理不顺带改动查询、分页、导出、权限或统计口径，按受影响组件检查实际显示并执行必要的局部验证。

## 不应随意修改

- 不要提交 `.env`、`data/`、`reports/`、`logs/`、`tmp/`、Excel/PDF 原始资料或任何生产密钥、Token、Cookie。
- 不要手工编辑 `frontend/dist/` 或运行时生成数据。`vendor/echarts.min.js` 由前端构建脚本同步。
- 不要在未核对业务和设备映射时改变报表统计范围、单位、日期归属、能耗仪表组或告警设备范围。
- 不要让浏览器获得数据库密码、LLM 密钥、邮箱密码或外部系统授权信息。
- 工作区中已有的未提交修改默认属于用户或其他会话；不要覆盖、回退或夹带提交。

## 文档维护

- 开始任务时只读取 `docs/` 中与任务直接相关的文档。新增共享文档确认未被 `.gitignore` 排除，链接目标应随源码可获取；内部原始资料继续保持忽略。
- 只记录对后续开发有用的结果：架构、UI 约定、性能优化、技术决策和当前进度分别写入 `docs/project/` 中的对应文档。
- `docs/project/progress.md` 保持简洁，只保留“当前状态、最近完成、当前问题、下一步”；不记录聊天过程。

## 通用外部数据源（P13）

- P5 正式 Runtime、Repository、UnitOfWork、队列与租约统一使用 PostgreSQL；连接在 `database/postgres.py`，独立迁移在 `postgres_migrations/`，离线迁移/校验工具为 `scripts/migrate_sqlite_to_postgres.py`。Web/Worker 只用 cutting_app，维护工具显式用 cutting_migrator；不把状态 PG 注册到生产数据源，不双写/自动回退，不在普通启动/请求安装 Schema。PG历史迁移只追加，SQLite仅保留离线历史与一致性导入工具。

- 新增 SQL Server/MySQL 数据库优先通过 `DatabaseSourceRegistry` 注册；管理页“其他数据源”仅登记连接，不自动创建产线、菜单、报表或授予业务权限。新产线仍沿用现有多产线注册与 Provider 机制。
- 新增连接统一使用 `models/database_sources.py` 的配置模型，SQL Server 使用 `SqlServerClient(config)`，MySQL 使用 `MySqlClient(config)`；不在业务 Service 中散落连接字段，不为每个新库复制连接池。专属 SQL、参数及字段映射归业务 Repository，不统一 SQL 方言。
- Registry 从 `DatabaseServices.database_sources` 请求/操作快照获取；Repository 固定本次操作的客户端，Service 通过装配显式接收 Repository。产线引用从统一目录合并；共享用途在 `database_runtime.source_bindings` 明确登记，不扫描源码推断引用。
- 动态连接仅保存于 `data/settings.env` 的 `DATABASE_SOURCES_JSON`，复用原保存锁、0600 原子写入、空密码保留和失败保留旧快照；新数据源的全部连接选项随快照热加载。所有生产连接及其选项统一随 Registry 快照加载；旧 SQLSERVER/WELD 配置通过一次性工具迁移，不保留正式运行兼容路径。
- 新操作使用最新 Registry，旧操作和旧 Repository 保留原配置/客户端。不能在配置发布时关闭仍被引用的旧池；MySQL 客户端最后释放或进程退出时关闭资源，在途会话返回后才清理池。
- 连接测试仅执行固定只读 `SELECT 1`；新增数据源不自动获得任意 SQL 查询能力，现有 Workbench 的生产库范围、管理员鉴权及令牌要求保持不变。响应/日志/诊断禁止回显密码或完整 `DATABASE_SOURCES_JSON`，前端类型从 OpenAPI 生成。

- BM 船名关联固定使用可选的 `bm` SQL Server 数据源；启用后由 `BmProjectRepository → ShipNameService → QueryService` 批量精确映射完整工程号，使用共享有界缓存。停用/未登记不影响启动和现有生产查询，启用时缺失或冲突映射保留任务、船名为空。禁止模糊匹配、截前缀、重复任务或以船名替换工程统计键。
- 切割报表的 `ship_no` 保留接口字段名、显示 BM `ProjectName`；PDM/派发船号保持原语义。日报船名由编号迁移单独保存，补名称不得改变任务修订号与确认记录。页面、导出和分析共用映射，确认/撤销不依赖 BM 可用性；连接切换须同步重建受影响设备服务并保留在途引用。
