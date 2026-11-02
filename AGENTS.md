# 项目开发指南

## 项目用途

本项目是面向内网的切割生产智能产报系统，提供领导、车间和设备报表，工人任务确认，智能分析与告警，报表导出与自动发送，以及切割指令文件管理。

## 技术栈

- 前端：Vue 3、Vue Router、Vite、Soybean Admin、Naive UI、UnoCSS、ECharts。
- 后端：Python、FastAPI、Pydantic、Uvicorn、Jinja2。
- 数据：SQL Server（切割/HR/PDM）、MySQL（焊接）作为外部数据源，SQLite 保存系统元数据、快照和操作记录。
- 报表：单文件 HTML、ECharts、Playwright/Chromium PDF 转换，可选 OpenAI-Compatible LLM 分析。
- 测试：Pytest 和 Node.js 内置测试运行器。

## 核心目录

- `backend/app/api/`：HTTP 路由、请求校验和权限边界。
- `backend/app/services/`：业务流程、统计、外部集成和报表生成。
- `backend/app/database/`：SQL Server、MySQL 和 SQLite 客户端、仓储与事务管理。
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
- SQLite 是应用自有状态库；多仓储原子操作应遵循 `docs/design/SQLite仓储与事务设计.md`。
- 生产式部署保留一个 Web（`app.main:app`）与一个独立 Worker（`python -m app.worker`）。Web 同源提供前端和 API，不启动后台消费者；不得用多个 Uvicorn workers 替代独立 Worker。
- 管理员与工人路由、菜单和后端 API 权限必须一致；不能仅依赖前端隐藏实现授权。
- 页面查询、HTML/PDF 导出和智能分析应共用同一统计口径、时区和区间定义。
- 管理页保存的运行参数写入 `data/settings.env`，密码不回显；数据库页全部配置及自动化/SMTP 参数按新操作热加载，其余启动参数修改后重启生效。

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
- 数据库层直接使用 `core/` 的取消、期限和锁机制；旧 `services/` 同名入口仅导出同一实现，不能复制注册表、ContextVar 或重入状态。SQLite 执行租约和提交围栏继续留在 `database/sqlite.py`。
- 条件查询契约、字段描述和通用值校验在 `models/conditions.py`；数据集字段/排序列映射在 `database/condition_fields.py`，SQL 编译和 Mock 求值在 `database/condition_query.py`。`services/condition_service.py` 提供公开 schema 和旧调用入口，Repository 不反向引用该 Service。小型响应封装不必为消除重复而建立公共框架。
- 告警事件归并、发送认领、状态转换、唯一性和租约判断必须在原事务内完成；扫描窗口、调度日期、模型/文件生成、SMTP 和重试策略由现有 Service 编排。不能为缩短 Repository 把原子校验移出事务。

### Runtime、配置与事务

- `application_runtime.build_runtime(settings, role=...)` 统一装配进程共享资源，`build_device_runtime` 装配设备专属服务。Runtime 负责对象连接与生命周期，不承载新增业务规则；业务方法接收所需服务/仓储，不自行查全局 Runtime 或重新构建它。
- Web 只在 `app.state.runtime` 挂载类型化 `ApplicationRuntime`；API 通过 `application_state(request)` 获取进程共享资源，通过 `runtime_state(request)`/`Depends(runtime_state)` 获取 `DatabaseServices` 请求快照。旧式平铺 state 仅在公共入口兼容，不在新路由继续散落属性探测。Runtime 上保留的初始数据库服务引用供旧调用方使用，不能代替热加载快照。
- Web 通过 `api/runtime.runtime_state` 固定单请求数据库服务快照，通过 `device_state` 选择切割设备；Worker 通过 `database_context` 在新操作边界选择快照。在途操作保持原引用，不混用新旧服务或直接改写已发布对象。
- `config.py` 保留不可变 `Settings`、字段元数据、校验、连接配置及设备配置派生；环境解析复用 `configuration_sources`。业务代码使用传入的 Settings，不能另读环境或另写默认值。启动全局 `settings` 不代表当前热加载状态。
- 热加载复用 `DatabaseRuntime`、`AutomationRuntime`；保存复用 `runtime_settings_store` 和进程锁，保持“锁内读改写/保留空密码 → 校验并准备候选 → 原子写文件 → 发布快照”。读取或构建失败保留旧快照，诊断用 `inspect()`，不因读取诊断触发热加载。不自行扩大热加载字段范围。
- SQLite 业务连接统一经 `SQLiteDatabase`/`SQLiteRepository`；跨仓储原子写入使用 `UnitOfWork` 绑定同一连接并由外层提交。Service 不持有原始连接、不自行提交；单仓储原子校验/写入由仓储完成。编号迁移复用 `database/migrations/`，不在路由或后台任务临时建表。
- SQL/LLM/SMTP/文件等耗时外部操作不得放进 SQLite 写事务；保留现有任务状态、幂等、未知结果保护和失败补偿。不要用事务回滚假定外部副作用已撤销，详见 [SQLite 事务设计](docs/design/SQLite仓储与事务设计.md)。
- 两进程通过同机本地 SQLite WAL 和共享文件卷协作；保留 Web OS 锁、Worker 租约/提交围栏、持久化队列及现有共享容量，不依赖跨进程内存对象或为每台设备新增常驻线程。不扩展为多主机或网络共享卷部署。

### 多产线与复用

- 切割设备映射以 `configuration/devices.json` 和 `device_config.py` 为准，由 `settings_for_device` 派生配置；类型/独立产线登记复用 `production_lines.py`。新增实例必须同时落实配置、服务装配、目录和测试，不能只增加名称；业务 SQL 仍放对应 `database/queries/` 或仓储。
- 复用现有 `ProductionReportProvider`、`QueryCache`、`CacheRevision` 和报表快照能力；Provider 只统一访问/刷新契约，不强行统一切割与焊接的指标、日期、单位和持久化结构。焊接保持独立只读 MySQL 仓储及服务，不塞入切割 `DeviceRuntime`。
- Provider 提供 `report_types/source_identity/fetch_summary/fetch_detail/refresh_summary/refresh_plan`；查询类型和明细结果由产线保留，后台查询参数须能 JSON 序列化。跨服务调用公开方法，不能访问其他 Service 的私有方法。`requested_at` 是 Worker 任务提交时间，沿用现有刷新合并语义。
- 新独立产线在 `production_lines.py` 登记 `service_key`、启用配置和能力，实现专属 Repository、Service/Provider，并在类型化 `DatabaseServices/ApplicationRuntime` 及数据库装配/热加载中接入；按需增加 API/页面和隔离测试。已有 Worker 按注册表消费 `refresh_plan` 和 `report_snapshot_refresh`，不要为每种产线增加调度分支或缓存框架。切割实例仍使用设备目录，切割月报保留原 bootstrap/AI 调度。
- 普通查询缓存用有界 `QueryCache`，缓存键或服务实例必须隔离产线/设备、来源、规范化条件及必要版本；可变返回值由调用方复制。领导月报直查和焊接短期统计复用该缓存；领导月快照的新鲜度缓存仍保留专属规则，焊接 AI 结果缓存仍按业务版本独立保存。
- 通用持久化快照复用 `ReportSnapshotService/ReportKey`、`CacheRevision` 和原 SQLite 队列，键包含产线、类型、日期、实际筛选/分页/排序、来源、schema 及权限范围。保留焊接页面快照优先、冷页异步准备、过期标识、失败保留与源信息采样；导出/下钻保持现场查询，不用旧快照绕过实时校验，不强制各业务使用相同 TTL。
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

## 前端设计开发规范

- 沿用 Soybean Admin + Naive UI + UnoCSS；新增或修改页面前，先检查 `frontend/src/theme/`、`assets/style.css`、`uno.config.ts` 和已有公共组件，不另建独立设计系统。
- 颜色、背景、文字、边框和阴影使用语义化主题变量；Naive UI 变量由 `theme/reportVariables.ts` 桥接，避免重复硬编码主题色，并检查亮色、暗色及主题色切换。
- 优先使用现有字号变量及 4/8/12/16/24px 间距：辅助文字和控件 12px、正文 13px、区块标题 16px；首页指标保留 30px 变体。报表筛选控件默认 40px 高，控件圆角跟随 Naive 主题，卡片 8px、报表面板 10px；图表尺寸、紧凑表格和特殊业务布局允许明确的局部例外。
- 表格、筛选栏、指标卡和图表面板优先复用 `AppReportTable`、`StandardReportQuery`、`ReportPeriodPicker`、`HomeKpiCard`、`ReportTrendPanel`，先完善现有组件，避免重复实现。
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

- 开始任务时只读取 `docs/` 中与任务直接相关的文档。
- 只记录对后续开发有用的结果：架构、UI 约定、性能优化、技术决策和当前进度分别写入 `docs/project/` 中的对应文档。
- `docs/project/progress.md` 保持简洁，只保留“当前状态、最近完成、当前问题、下一步”；不记录聊天过程。
