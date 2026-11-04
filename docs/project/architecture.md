# 当前系统架构

## BM 旧 TLS 与区间报表确认读取（2026-11-03）

- 通用 SQL Server 数据源的 `legacy_tls` 默认关闭，仅显式开启的数据源批量只读查询进入一次性子进程。专用 OpenSSL 配置允许旧 TLS/签名算法，不修改宿主机或 Web/Worker 进程环境，不影响其他数据源、HTTP 或 SMTP 的 TLS 策略。凭据通过私有管道传递；取消注册表负责终止在途子进程，查询预算及操作期限继续生效，结束后回收资源。直接连接、流式及多结果集接口不在此兼容模式范围内。
- `RangeReportService` 显式接收设备范围的 `ConfirmationRepository`，流水线装配时提供专属确认仓储；不能从只负责日报的 `WorkshopReportRepository` 调用确认查询。回归覆盖真实 Runtime 的两台设备，验证 BM 故障时任务、指标及设备隔离的确认状态仍可用。

## P14 维护边界（2026-11-03）

- 保留 ApplicationRuntime、DeviceRuntime、DatabaseRuntime、数据源 Registry、QueryCache、CacheRevision、Provider 与报表快照的独立职责。
- WorkshopRepository 仍被装配、流水线、自动化及嵌入式测试使用；API 平铺 state 回退与初始服务引用仍被嵌入式运行方式使用，继续保留。
- 条件查询仅由 condition_service 提供 HTTP schema；模型校验、字段映射和 SQL/Mock 编译直接从所属模块复用。P13 数据源登记、业务引用保护和保存机制不变。

## P04 基础依赖与数据库职责（2026-11-02，兼容入口按 P14 更新）

- `core/request_cancellation.py`、`report_deadline.py`、`process_lock.py` 原样承接共享取消注册表、执行期限和 OS 锁。SQL Server、MySQL 及迁移 runner 直接依赖 core；旧 services 转发路径已删除，所有调用方直接导入 core，不复制状态。PostgreSQL 的执行租约 ContextVar、连接检查和提交围栏仍在数据库层。
- `models/conditions.py` 集中条件请求模型、字段描述、操作符和值校验；`database/condition_fields.py` 保存服务端数据集字段及排序表达式；`database/condition_query.py` 编译参数化 SQL 并提供 Mock 筛选/排序。`condition_service.py` 仅提供公开 schema，基础函数由调用方直接导入所属模型/数据库模块，不改变公开字段、错误、分页及排序口径。字段表达式不能由客户端提供，也不放进 core。
- 已检查 `AlarmRepository`、`AutomationRepository` 及对应服务。告警扫描/回查、高水位、模型和文件生成已由 `AlarmMonitorService` 编排；计划日期、跨仓储报表准备、SMTP、附件和退避已由 `AutomationService` 编排，无须搬迁。仓储中的事件指纹/去重、去抖关联、游标、状态认领、部分收件人结果、人工核对审计及关联状态更新依赖同一事务，保持原实现。
- 没有按行数拆分这两个 Repository，也未修改迁移结构、Worker 恢复或 P02/P03 的装配和报表实现。新增调用优先使用 core 及数据库查询模块，仍有真实调用方的其他兼容入口继续保留。
- 定向检查覆盖取消归属与线程传播、期限预算、请求容量、跨进程配置锁、迁移活动进程保护，以及条件校验/SQL/Mock/SQLite 查询和 HTTP schema。取消 HTTP 测试的设备夹具已在后续修复，独立中间件/查询线程上下文验证继续保留。

## P03 报表分析与生成职责拆分（2026-11-02）

- `services/report_analysis/leadership.py`、`workshop.py`、`equipment.py` 分别构建各自的事实、比较、异常及两张完整分析表；`common.py` 共享格式化、规范化与版本摘要算法。纯上下文构建不访问数据库或调用模型。
- `ReportAnalysisService` 保留统一入口、来源版本校验、LLM 调用、缓存/单飞/跨进程协调、取消与期限、输出展示及持久化。原有公开版本函数及导入入口保持，不调整提示词版本。
- `services/report_generation/` 按领导、车间、设备、自定义报表组织内容准备；`common.py` 提供周期/元信息规则与 `PreparedReport` 返回类型。各类型显式接收现有 Service、Repository 和进度/分析/停止回调，不传入整个 Runtime 或重新创建协作对象。
- `ReportPipeline.run()` 统一分派任务，保留设备校验、取消/超时、AI 失败降级及恢复执行上下文；内容准备返回后，统一渲染并在同一 UnitOfWork 中保存报告和完成状态。对象装配继续保持原有构造方式。
- 删除 `run()` 无法到达的旧日报设备能耗分支；保留可到达的历史日报兜底及其设备比较行为。车间单日/区间共用日报统计输入转换，不合并各类报表的业务口径。
- 定向验证包含拆分前固定的 8 组分析输入/来源版本摘要、6 组报表内容/进度摘要，以及模型缓存/并发、降级、控制信号、区间口径、多设备、Runtime 复用、离线 HTML、PDF 和跨进程恢复。API/Worker 内其它日期助手及既有装配耦合不在本次扩改范围。

## P02 Runtime 与依赖注入（2026-11-02）

- `ApplicationRuntime` 是明确列出共享仓储、服务、后台控制对象及配置 Runtime 的 dataclass；Web 入口只挂载 `app.state.runtime`，移除遍历 `vars()` 动态平铺属性的装配方式。Web/Worker 继续使用同一个工厂，仅恢复步骤按角色区分，不拆成两套服务图。
- `DatabaseServices` 是 frozen dataclass，由 `DatabaseRuntime` 发布；焊接独立变更使用 `dataclasses.replace` 保留切割对象，在途操作继续持有旧快照。下载锁/容量、能耗完成重试状态的复用保持，冻结只约束快照字段绑定，不冻结内部 Service 或缓存。
- API 共享资源统一经 `api.runtime.application_state`，热加载服务经 `runtime_state` 缓存到 `request.state.database_context`；设备选择和焊接服务选择复用此快照。设备/产线目录直接使用 `Depends(runtime_state)`，可覆盖依赖；现有直接调用 getter 与 Depends 在同一请求中使用同一快照，不要求批量改写所有端点签名。
- 自动化与配置诊断通过构造参数显式注入 `DatabaseRuntime`，不再创建后补挂依赖；设备 Runtime 明确持有确认仓储，流水线复用设备日报仓储，避免每次属性访问创建确认仓储及重复创建/初始化日报兼容门面。直接构造 `ReportPipeline` 时仍保留原仓储初始化行为。
- 兼容保留：嵌入式应用平铺的 state、仅提供 WorkshopRepository 的用户/会话调用、Runtime 初始服务引用、独立构造 Pipeline/AutomationService、旧 Worker 线程模式与旧流水线签名。仍有实际调用和测试覆盖，不据此扩大兼容分支。
- 本阶段未拆配置保存业务编排，未重做产线注册或移动基础机制。Worker 对私有执行方法的调用、Pipeline 内其余协作服务的构造、初始服务引用的最终退役仍需后续按实际用例渐进处理。下方 P01 问题表是梳理时的基线，已实施部分以本节为准。

## P01 后端边界与后续治理（2026-11-02）

本节以当前 `backend/app/` 的导入关系、入口装配和实际调用为依据；可执行开发约束统一放在根目录 [AGENTS.md](../../AGENTS.md#后端模块边界p01)。P01 仅制定规范和记录问题，不改业务、接口、表结构、统计口径或进程模型。

### 当前依赖与组织

```text
main.py（Web）  scheduler.py（调度）  worker.py（消费×N）  automation_worker.py（自动化/邮件）
  ├─ api/       └─ 周期入队          └─ workers/       └─ AutomationService
  └──────────┬─────────────────────────────────────────────────────────┘
             services/application_runtime.build_runtime(role=...)
               ├─ 共享仓储、外部服务、配置 Runtime
               └─ 统一目录 → 类型构建函数 → lines[line_id]（切割/焊接实例）

业务调用：api/ 或 workers/ → services/ → database/ → PostgreSQL / 外部数据库
外部 HTTP、文件、SMTP、LLM：由 services/ 中现有客户端与业务服务协调
models/、Settings：供各层使用；Runtime 装配是上述单向业务调用的例外
```

- `main.py` 获得 Web OS 锁后装配并挂载 `app.state`，不启动后台消费者；`worker.py` 获得 PostgreSQL 租约后恢复后台任务，使用共享有界执行池。Web 的派发/下载恢复仍归 Web，报表/能耗恢复归有效 Consumer，邮件发送恢复归有效 Automation。四角色只支持同机本地共享卷。
- `services/application_runtime.py` 装配自有 PostgreSQL 仓储、共享服务与配置 Runtime；`device_runtime.py` 装配设备查询、统计、分析、流水线和后台控制对象。其对 `workers/` 的导入属于装配职责，不是业务 Service 反向调用调度层。
- `services/frontend_static.py` 虽位于 services，实际是 FastAPI/Starlette 静态资源适配，只供 Web 入口使用；P01 保留位置，业务 Service 不依赖 HTTP 框架的约束不要求为它新增一层包装。
- `api/runtime.py` 在 `request.state` 缓存一次 `database_context`；`api/devices.py` 验证唯一 `device_id` 并选设备。`DatabaseRuntime` 准备/发布服务快照，`AutomationRuntime` 管理自动化/SMTP 快照；业务操作保持已选引用。新功能沿用这些边界，不另建全局服务定位器。
- `config.py` 的 `Settings` 为 frozen dataclass，但模块导入读取统一环境来源并更新 `os.environ`。复用 `configuration_sources` 和字段元数据；默认优先级为 `settings.env > process > .env > defaults`（显式禁用 dotenv 时遵循现有分支）。新增业务代码不重读环境、不修改全局 Settings。
- PostgreSQL 的连接、租约检查和提交在 `database/postgres.py`；多仓储原子用例复用 `unit_of_work.py`，例如自动化创建报表任务及流水线完成写入。事务内的确认归属/冲突检查属于数据完整性边界，不能为“瘦仓储”移到事务外。正式状态 Schema 迁移统一走 `database/postgres_migrations/`，`database/migrations/` 仅保留离线 SQLite 历史准备。
- 全部产线实例来自 `PRODUCTION_LINES_JSON`，由 `device_config.ProductionLineCatalog` 校验；类型能力在 `production_lines.LINE_TYPES` 声明，Runtime 按类型构建 `lines[line_id]`。`production_reports.py` 已有 Provider 契约，`report_snapshots.py` 已有快照访问机制；它们不要求统一各产线统计模型。焊接使用独立只读 MySQL，与切割 SQL Server 分开。

### 主要问题（按后续变更影响排序）

以下是耦合和维护风险，不等同于已复现的业务故障。阶段归属是本次梳理的承接建议，P02～P04 的具体改动应按后续任务确认。

| 顺序 | 代码依据与现状 | 影响与后续边界 |
| --- | --- | --- |
| 1 | `application_runtime.py` 用 `SimpleNamespace` 聚合服务，热重建闭包浅拷贝旧对象；`device_runtime.py` 通过 pipeline 属性暴露查询/能耗服务；`worker.py` 调用 `_refresh`、`_analyze_*`、`_claim`、`_process` 等私有方法 | 生命周期、热加载和业务对象连接关系隐含，新增资源容易遗漏共享锁或在途引用。**P02** 梳理装配/生命周期与公开执行边界；保留双进程、租约、容量和快照语义，不预建 DI 容器 |
| 2 | `api/database.py` 同时做配置密码合并、锁、候选构建、持久化发布和直连探测；`api/welding.py:seam_export` 穿过 Service 访问 `repository.client.snapshot()` 并组织明细导出；`api/analysis.py` 仍组织多类报表查询/分析数据 | HTTP 边界与业务/资源流程混杂，Web 与后台复用时易复制流程。配置发布归 **P02**；业务编排下沉归 **P03**。已有 `WorkshopService`、`WeldingExport` 和 `analysis_execution` 可复用，不要求所有一行仓储调用都包 Service |
| 3 | `database/pdm.py`、`downloads.py` 导入 `services/condition_service` 的字段/SQL 编译；`sqlserver.py`、`welding.py` 导入 `report_deadline` 和 `request_cancellation`；迁移 runner 导入 `process_lock` | SQL 条件模块混有业务字段、公开 schema、SQL 和离线求值；取消/期限/锁则实为基础机制。**P04** 按职责收敛，必要时仅把稳定无业务机制移入 `core/`，不得把条件业务字典整体搬到 core 或复制一份来消除导入 |
| 4 | `_year_ago` 在 `api/analysis.py`、`services/pipeline.py`、`workers/current_report_refresh_worker.py` 各有实现；多路由重复响应包装；`ReportPipeline` 还内部创建查询、能耗、分析和渲染协作对象 | 日期逻辑跨页面/导出/后台存在漂移风险，装配分散增加理解成本。**P03** 优先复用实际相同的日期/统计规则，并与 P02 装配边界衔接；**P04** 再判断低风险重复是否值得合并。简单响应字典暂留，不为它另建框架 |
| 5 | `database/workshop.py` 多继承兼容门面仍被 `application_runtime.py`、`pipeline.py`、`automation_service.py` 等引用；多个 Worker 同时保留 managed/队列模式与独立线程模式，`report_worker.py` 保留旧 Pipeline 签名兼容 | 新旧边界并存，直接删除会影响嵌入式调用或测试。**P04** 先盘点调用方和替身再逐项退役；P01 不认定其为死代码，新功能不扩大兼容面 |

根目录 AGENTS 原先“后台线程依赖单个 Uvicorn 进程”的表述已在 P01 纠正。既有历史记录仍有过时描述，例如 `backend.md` 的旧设备环境覆盖说明、D-038 的“不热改数据库配置”（已被后面的热加载补充替代）、`runtime_settings_store.py` 的新文件头注释。当前以代码和本节边界为准；P02/P04 按涉及范围同步整理，不在 P01 扩大修改。

### P02～P04 的承接与验证范围

- **P02：Runtime/配置。** 聚焦装配、热重建、共享资源保留和配置保存职责；定向验证 Web/Worker 角色恢复、配置并发保存、失败保留旧快照、在途服务引用。不得改为多 Web/多主机或新增配置入口。
- **P03：业务编排/事务。** 聚焦路由中残留流程及页面、导出、后台的重复业务计算，复用现有 Service/Repository/UoW；定向覆盖对应 HTTP 契约、统计结果、确认权限、设备隔离和事务回滚。事务机制已存在，无需重新实现。
- **P04：公共机制/兼容收敛。** 聚焦按职责处理反向导入、必要重复和实际有调用方的兼容路径；仅在复用收益明确时提取基础机制，迁移需覆盖实际调用方。取消、期限、租约与未知副作用保护不得弱化。

P01 仅检查 Markdown 差异、引用路径和与上述代码/现有事务及双进程说明的一致性；不启动应用、不连接生产系统、不运行测试、构建或迁移。后续阶段不视为已授权实施。

## 焊接产线接入（2026-10-26）

焊接使用独立MySQL只读连接、仓储、统计和API，复用登录、主题、PDF及模型资源控制，不进入切割设备运行时；统计预热和快照刷新复用独立 Worker 的现有队列。目录按设备类型分组，原切割编号、接口和历史范围保持。焊接连接接入现有数据库配置快照，单独保存不重建切割服务，旧请求保留原快照。单位、日期、待核实指标、导出、部署和验证说明见 [焊接接入](welding-integration.md)。

> 更新日期：2026-10-26。本文档记录当前实现，历史阶段说明保留当时背景；新增开发约束以根目录 AGENTS.md 为准。

## 产线注册与前端 API（2026-10-27）

`services/production_lines.py` 集中登记产线类型、能力、首页和业务 API 范围；所有实例统一从 `PRODUCTION_LINES_JSON` 加载，服务按稳定 line_id 从请求快照的 lines 集合选择。接入新类型时登记业务构建函数、类型能力及对应前端业务路由；同类型新实例只配置目录及 source_key，不修改公共运行时。不引入插件加载器或新的配置管理入口。

前端公共选择、路由、标签页、预加载和配置页按产线类型处理，首页优先使用目录的 `home_path`；类型后备入口集中于 `utils/productionLines.ts`。业务 API 拆到 `api/auth.ts`、`devices.ts`、`cutting.ts`、`welding.ts`、`energy.ts`、`alarms.ts`、`dispatch.ts`、`automation.ts`、`configuration.ts`、`designer.ts` 和 `system.ts`，统一使用 `client.ts` 的同源请求、取消、错误和会话过期处理；`reports.ts` 仅保留兼容导出。

`X-Production-Line` 仅作为能力范围提示，可以拒绝不适用的业务，不能授予权限；移除或伪造该头不能绕过端点的管理员/工人鉴权。当前管理员仍可访问所有登记产线，没有自行增加车间 ACL、用户授权字段或授权管理页；未来分车间授权须定义用户与产线关系并在服务端校验。

生产部署的入口 HTML（含 SPA 子路由）使用 `Cache-Control: no-store`；`/assets` 下按 Vite 命名带内容哈希的 JS/CSS 使用一年 `immutable` 缓存。普通资源和错误响应不套用长期缓存。

## 多产线统计快照（2026-10-27）

Provider契约和统一产线服务解析接通切割领导月报与焊接统计。切割保留旧快照表及原刷新/确认逻辑，共享原有QueryCache实现；焊接增加L1→PostgreSQL→MySQL统计访问，复用CacheRevision和现有Worker队列。完整统计包原子持久化，Web重启可继续读取；页面使用按类型隔离的快照：日报/设备页共用完整聚合，工件/作业页按分页和排序隔离；导出与下钻继续现场查询。快照身份、配置隔离、期限、资源上限、验证及回滚步骤见 [多产线报表数据基础设施](multi-line-report-data.md)。不增加独立Worker或源库表结构。

## 总体结构

```text
Vue 3 页面
  ↓ 同源 HTTP API
FastAPI 路由与权限边界
  ↓
业务 Service
  ├─ 报表查询、统计、智能分析、HTML/PDF
  ├─ 工人确认、文件派发、邮件自动化
  └─ 告警监控与报告
  ↓
Repository / 外部系统客户端
  ├─ PostgreSQL：应用元数据、快照、任务和审计记录
  ├─ SQL Server：生产报表源
  ├─ HR / PDM：人员与切割文件源
  └─ 能管、激光魔盒、SMTP、LLM：外部服务
```

生产式部署由独立 Web 同源提供前端静态资源和 API；Scheduler 生产周期任务，Consumer 消费后台任务与报表，Automation 扫描自动化计划、补偿并发送邮件。四者复用 `application_runtime.build_runtime` 与产线 Service，各自持有连接和资源，通过同一个 PostgreSQL 状态库及本机共享文件卷通信。Web 生命周期不启动任何后台线程，不恢复后台运行态。

当前支持一个 Web、一个 Scheduler、1～N个同机 Consumer 与一个 Automation，默认单Consumer；Web 用 OS 锁阻止重复启动，保留进程内 HTTP 请求取消注册表。三个后台角色各用 PostgreSQL 30 秒独立租约和 5 秒心跳协调同角色唯一执行者，失效执行者的仓储读取与事务提交被拒绝。多个设备共用固定的 4 个后台操作消费者、10 个分析消费者，以及报表/自动化/邮件各一个消费者；设备控制对象只提交持久化请求，不创建独立线程。告警原有每次执行的续租线程仍保留，其数量受操作池上限约束。

`background_tasks` 保留周期轮次和按需刷新请求。就绪等待队列最多 256 项，超额或失败任务显式转 deferred，记录原因并在容量释放或退避到期后重新准入，不丢弃周期分析轮次。已完成的辅助任务及分析执行记录保留 30 天；未完成任务保留。后台消费者不占 Web 的执行池。Web 继续使用 AI/PDF/SQL/鉴权独立限额 10/2/8/8。全局模型容量再以共享 OS 槽锁限制，两个进程和设备合计不超过同一个 LLM_MAX_CONCURRENT；前台等待优先、普通后台配额及周期分析使用完整空闲容量的策略保留，跨进程同版本等待也能提升生产者优先级。

生产查询与领导快照缓存仍留在各进程内。后台成功刷新后发布设备级文件版本，Web 的查询缓存键和领导快照缓存据此换代，避免拆分进程后继续读取旧本地缓存；普通请求只检查轻量文件版本，不逐次查询状态库。能耗日快照使用比较后写入，防止跨进程计算覆盖刚保存的结果。

`/api/v1/health` 保留主要字段，HTTP 状态表示 Web 与 PostgreSQL 状态库的可用性；新增 background_ready/background_status 区分后台正常、停滞、失联和停止。后台状态来自持久化心跳并缓存 2 秒，Web 不依赖本地 Worker 对象。三个后台分别用 `python -m app.scheduler --health`、`python -m app.worker --health`、`python -m app.automation_worker --health` 探针；汇总状态只有三个角色均正常时就绪。

前端接入 Soybean Admin v2.3.0 的布局基础及完整官方主题 store/设置抽屉，使用 Naive UI、Pinia 与 UnoCSS；上游代码和许可在 `frontend/src/vendor/soybean-admin/`。保留既有 Vue Router 静态路由、同源 Cookie 认证、设备上下文和业务服务，不使用模板演示 API 或 Token 认证。主题由官方 theme store 注入完整 UnoCSS RGB 色阶，并驱动 NConfigProvider；外观、六种布局、标签栏、通用设置及四套预设全部接入，业务路由与会话使用适配层；首页 ECharts 监听主题重绘，切色不触发数据查询。既有 JavaScript 业务与上游 TypeScript 布局并存，仍以单一 pnpm 工程构建；构建和源码包携带第三方许可。

## 主要模块关系

- 生产报表源统一为 SQL Server，移除 Excel 读取、本地 SQL 兼容执行及文件挂载；显式 demo 模式继续用于隔离测试。旧 `APP_DATA_SOURCE_MODE=excel` 配置会明确拒绝启动，须迁移为 sqlserver；不自动回退或改写历史快照来源。
- 全部产线通过 `PRODUCTION_LINES_JSON` 保存稳定 ID、物理设备范围和 `source_key`；连接只来自 `DATABASE_SOURCES_JSON`。`DatabaseServices.lines` 统一管理全部实例；历史 `device_id` 继续保存原产线键，HTTP 切割请求仍须明确指定唯一 `device_id`，焊接请求必须指定 `X-Production-Line`。
- 车间日报按日期与设备唯一保存；报表任务、历史列表、确认审计和分析源版本按设备隔离，领导月快照按月份与来源指纹隔离。迁移前自动备份 SQLite；明确的单设备历史结构归属 Cutter01，歧义或冲突拒绝自动迁移。任务在唯一 production 来源内以 `(scope_machine_no, TASK_ID)` 为复合键，同号任务跨设备独立确认、统计及审计。
- Cutter02 不绑定能耗仪表，任务正常统计，能耗保持空值并显示“暂未接入”；告警按 START_TIME 从 2026-10-12 00:00:00（Asia/Shanghai）监控。两台设备拥有独立告警游标与租约，后台运行仍受原全局开关控制。
- 三类标准报表共用查询区间、中文日期显示、表格列宽规则和智能分析面板。页面、导出和分析必须保持同一数据口径。
- 统一设备报表的单日与区间任务能耗共用 `EnergyPeriodService` 的设备范围、仪表匹配和任务区间计算；区间报表只在计算后按日期汇总。同一天两种查询方式应返回相同的任务能耗和分析源版本。
- 智能分析由报表 API 提供 `analysis_source_version`，服务端根据规范化业务数据计算 `data_version`，再查询 PostgreSQL 缓存。三类标准报表发送六项后端确定性事实和页面两张完整数据表：领导报表为区间每日明细与近 12 个月月度汇总，车间报表为任务明细与工人工时汇总，设备报表为任务能耗明细与近 12 个月月度能耗汇总；仅发送报表展示所需业务字段，不发送内部标识和能管采样细节。
- SQL Server 模式下当前月领导报表、最近生产日管理员车间报表和设备报表每 300 秒后台刷新；模型启用时每轮强制重析，同版本结果覆盖缓存。自定义区间和工人确认快照仍按需处理。
- 报表与告警的大模型 HTTP 调用共用 PostgreSQL 用量统计：累计汇总与逐次明细原子写入，按配置时区的请求开始日期统计每日、每月用量。设置页第 5 个子页面“模型用量”展示每日/月汇总、场景/模型分布、输入/输出/总 token、请求次数、失败次数及平均耗时，不展示逐次调用明细；缺失用量保持未知，缓存命中不计调用。旧版累计保留但不伪造日期，明细启用时间单独保存；仅管理员可查询，不保存提示词、响应正文或密钥。
- 车间任务确认以 `(scope_machine_no, TASK_ID)` 为任务键，确认、撤销和管理更正在 PostgreSQL 留存审计记录，不写回生产库。
- 文件派发对用户是一体化流程：直接从 PDM 选择文件并创建任务，执行时复核版本和路径，再通过 FO 临时下载、校验、上传魔盒并指派机床。内部阶段和审计记录保持独立，推送成功后删除临时实体副本。
- 自动化计划与每次运行固定保存设备，报表队列按任务设备选择流水线，页面切换不改变计划。所有设备的自定义 SQL 均需要显式声明 `:machine_no`，参数由服务端固定绑定；标准报表直接支持设备范围。
- 报表自动化由计划、报表生成、邮件队列和失败重试组成。运行与 SMTP 参数由管理页写入 `data/settings.env`，通过跨进程文件锁原子保存，Web/Worker 按文件版本加载同一不可变配置快照；新操作即时生效。数据库页连接配置使用同样的跨进程版本传播（P3 容量字段须重启）：每个请求固定一个服务快照，新请求/任务切换后仍保留在途操作引用的旧服务；不修改已有报表、确认、派发和快照记录。
- 统一保留任务每天清理已终结的报告、分析版本、告警、派发和审计数据；文件删除通过 PostgreSQL 重试队列执行，活动记录和待发送邮件不进入清理范围。

## 不可破坏的边界

- 业务逻辑放在 service，数据库细节放在 repository，API 层不重复实现业务规则。
- 管理员/工人的前端路由和后端授权必须同步。
- 外部数据库和平台默认只读；应用仅修改自有 PostgreSQL 状态和受控运行文件。
- 密码、Token、Cookie 和 LLM/API 密钥不返回浏览器、不进入报表或普通日志。

设备隔离迁移、备份、回滚及待核实历史记录见 [多设备隔离修复与升级](multi-device-isolation.md)。


## 身份验证请求容量

`optional_user` 为异步依赖，PostgreSQL Session 查询及禁用后的会话撤销通过现有默认工作线程执行，不占鉴权池；Cookie、有效期、角色和请求取消归属规则保持。HR 状态先读取原有内存缓存；缓存未命中或写操作强制复核时，才通过原8并发鉴权池卸载 HR 操作，慢 HR 不持有默认线程。GET 的既有复核间隔、15秒故障缓存、256项 LRU 与固定查询锁不变，缓存命中的有效/禁用/故障状态都不需鉴权准入。

登录、HR目录、管理员密码二次验证及本地账号创建/初始化的 PBKDF2 继续或补齐8并发保护；依赖复核完成并释放名额后才运行受保护端点，避免嵌套占用。AI/PDF/查询限额10/2/8保持。`RequestWorkload` 的运行名额由同步操作结束释放；调度前取消可回收名额并防止迟到执行，AnyIO及原生asyncio取消不会在操作仍运行时放宽实际并发。

默认线程池仍由普通同步接口和轻量状态查询共享，慢状态查询 或其他同步请求过多时仍可能排队；未命中缓存的 HR 复核容量不足仍安全拒绝，不绕过复核或撤销有效本地管理员会话。前端现有按路由串行预加载保持，不以修改页面请求节奏规避后端鉴权问题。

## 配置解析与只读诊断

`configuration_sources.load_environment` 统一解析启动和保存目标，保持 `settings.env > process > .env > defaults`。`Settings` 字段工厂及环境键元数据复用于目标解析，避免复制默认值/转换规则；启动同时捕获真实来源。设备目录保持单一业务来源，原环境断言不变。

`ConfigurationDiagnostics` 从启动 Settings、`DatabaseRuntime.inspect()` 和 `AutomationRuntime.inspect()` 读取实际快照；inspect 不调用会加载文件的 snapshot。自动化请求边界与热加载共用 `AutomationConfigurationRequest` 验证，异常热配置保留旧快照。数据库来源通过 Registry 校验；公开值脱敏，不再从旧连接字段推导回退关系。

Worker 每5秒将自身安全快照和内部逐项 HMAC 比较值附在原租约心跳；Web 用该租约上下文生成自身比较值，包含凭据变化的差异也可识别。API/公开健康接口/健康命令不输出逐项指纹，页面版本为不含配置内容的加载批次号与热修订。失效或缺失 Worker 状态不能视为已生效；诊断采集失败不阻断 Worker 心跳。

新增管理员只读 `GET /api/v1/configuration/diagnostics` 和系统设置子页，不增加一级菜单或编辑能力。状态包含已生效、待重启、异常、可选未配置；按分类/名称/状态筛选，分别显示运行值、目标值、来源和生效规则。仅核对配置结构和运行快照，不连接外部系统。密码、账号、密钥、认证头、Cookie、URL 等仅显示安全状态，异常不透传原始输入。

P6 部署为 Web、Scheduler、Consumer、Automation 四角色，共享原数据卷；不增加配置中心或数据库表。冷配置重启四个进程，统一目录及数据源在新操作边界热加载；宿主机 `.env` 更新重建 app/scheduler/worker/automation，管理保存的数据库连接配置及自动化/SMTP 在下一轮操作热加载，P3 容量字段须同时重启四个进程。诊断状态无法替代宿主机注入检查或外部连通性验收。现行保留默认值与 `.env.example` 一致：告警发生后30天且已解除，其他保留策略不变。


## P1：统一产线、绑定与生命周期（2026-11-03）

唯一实例目录为 `Settings.production_lines_json → ProductionLineCatalog`，类型与能力声明在 `services/production_lines.py`；`configuration/production_lines.example.json` 仅为编辑示例。`devices.json` 仅由独立升级工具读取，正式运行不读取该旧目录，也没有 `DEVICE_CATALOG`、`SERVICE_LINES` 或 `runtime/context.devices/welding` 别名。

每个实例拥有 `id/name/type/source_key/enabled`。切割实例另外明确 `device_id`（物理设备）、`machine_no`（源查询范围）、`production_line_id`（原业务分组）、能耗仪表与告警起点。既有 `Cutter01/Cutter02/welding` ID 不变，状态库的历史 `device_id` 列仍保存这些稳定产线键；日报/确认原机床范围不重命名。切割物理编号、机床及历史别名全局唯一，防止已有按机床保存的确认/告警历史归属重叠。焊接 `device_nos` 可限定实例范围；共用 MySQL 的同类型实例必须配置互不重叠的范围，独立库可留空表示整个库。

`DatabaseSourceRegistry` 是全部 SQL Server/MySQL 客户端的入口。相同 source_key 复用客户端，变更配置只创建新客户端；生产 Service 不再从全局 SQLSERVER/WELD 参数建连接。HR、PDM 与魔盒分别由 `WORKER_SOURCE_KEY/FILE_SOURCE_KEY/DISPATCH_SOURCE_KEY` 显式绑定，BM 保留可选 `bm` 绑定。引用由目录和这些共享用途合并声明，阻止误删和引擎变更；启用实例/业务不可指向停用或不兼容的连接。

`ApplicationRuntime` 只持有进程资源与 `DatabaseRuntime`。后者发布 `DatabaseServices(catalog, lines, database_sources, ...)`，每个 `ProductionLineRuntime` 具有实际共享的 settings/Provider 契约；后台任务与历史切割报表流水线是独立可选能力。类型构建函数由简单映射登记，同类型新增实例不改 Runtime、Worker 或 API。未改变绑定/配置及业务依赖的实例复用；请求保留旧服务引用，旧 MySQL 客户端在最后引用释放后关闭，已借出的连接归还后才清理池。

Web 使用单请求快照，Worker 在每次调度、任务执行、报表领取、自动化操作选择快照。两个进程监测同一 settings.env 文件版本，无新增消费者或消息组件。公共 Worker 只分发实例的后台能力与 Provider，切割专属调度/执行留在 DeviceRuntime。任务仍使用既有持久化队列、租约、去重、取消与重试；新增来源标记防止旧队列任务写入新来源快照。快照以稳定产线 ID 与来源身份隔离；切割 AI 的缓存键、最新结果及源版本使用固定操作来源命名空间，旧在途分析不能覆盖新来源视图，公开业务版本算法保留。

目录与数据源保存继续使用原进程锁、候选验证和 0600 原子替换，发布失败保留旧快照。管理员 `PUT /api/v1/production-lines` 可提交完整实例目录；不能删除历史 ID 或更换既有物理归属，停用实例会停止新业务查询与调度，但历史报表/告警报告仍可读取和下载。数据库页面的原连接表单只是选中 source_key 的编辑视图，不再写旧连接环境键；停用实例不会顺带停用其他实例共用的数据源。同一来源在多个连接区块出现时只合并实际改动字段，冲突修改拒绝发布。

升级：先停止两个进程，执行 `python scripts/migrate_production_lines.py`。工具备份原 .env、settings.env、devices.json 及进程中的有效旧连接（备份目录0700、文件0600），保留既有数据源并转换旧生产/焊接/HR/PDM连接及实例。重复执行只验证统一配置，失败不写目标，不回退旧运行路径。首次新版本启动沿用编号迁移备份机制执行 `015_alarm_sources`：外部告警编号改为来源内唯一，按明确实例绑定迁移已有事件、归并指纹与游标，同时把原最新分析及版本绑定至升级来源，原 incident/report/analysis ID 不变；无法归属或游标冲突会拒绝启动。生产数据库不写入。

新增切割线：先登记 SQL Server 数据源，再添加具有唯一 ID、物理编号、machine_no、生产分组及告警起点的 cutting 实例，能耗配置按实际仪表提供。新增焊接线：登记 MySQL 数据源，添加 welding 实例及生产时区，按需指定 device_nos。不同库设置不同 source_key；共用库设置相同 source_key 并提供互不重叠的设备范围。均通过配置生效，不复制服务或路由。具体升级命令和回退边界见[配置参考](../deployment/配置参考.md)。

## P2：Worker 调度与空队列事务（2026-11-03）

继续保持单 Web、单 Worker、原队列容量与 P1 实例生命周期。`WorkerProcess` 保存周期任务的 monotonic 到期提示、间隔和来源，以及当天各产线/月份的对账确认；每轮仅保留当前启用实例与 Provider 实际提供的计划，状态不会随日期或历史来源累积。产线后台能力通过小型 `BackgroundSchedule` 契约调用同一个 Worker，不新增调度线程或另一套调度框架。领导快照 bootstrap 判断延迟至任务到期，能耗历史处理及 AI/告警/邮件业务执行不变。

首次遇到周期项先只读恢复 `background_schedules.next_due`，未到期直接跳过；真正到期后仍在原事务内推进进度、持久化入队。入队异常或未确认持久化会回滚进度，内存不提前记成功。`016_schedule_intervals` 仅为原表追加可空 interval，不新建调度表、不改原期限或去重键。旧行首次加载时登记当时的间隔并保留原 next_due；后续间隔改变从上次持久化轮次推算新期限，重启也能恢复。`due` 的独立用途仍仅提供持久化时钟判断，任务调度继续经原子 `schedule` 调用。升级沿用编号迁移的自动备份与事务，Web/Worker 一起更新；回退 P1 时恢复对应升级前 SQLite 备份，不能仅替换旧程序。

每日对账仍在既定小时提交最近两个月（本月由周期刷新承担），沿用原 `reconcile:产线:日期:月份` 去重键。成功入队或只读确认同键记录已存在后才记为当天完成；每个月独立，失败可在下轮重试，跨日自动清理提示。快照计划沿用原产线/报表类型/查询/来源键；已完成任务以 updated_at 加间隔恢复期限，活动任务至少等待一个间隔再检查，成功完成提交时直接用 Repository 返回的持久化时间更新提示。容量拒绝且无持久化任务时保留下一轮重试。任务完成后的间隔语义、队列去重和统计快照结构保持原样。

新增、停用、重新启用、来源/计划/间隔变化均在原 DatabaseRuntime 请求快照边界处理；未变更的调度项复用提示。`REPORT_BACKGROUND_REFRESH_SECONDS` 额外纳入原热加载白名单并重建受影响的业务配置，实际最小间隔仍为30秒，其他冷参数不扩大热加载范围。系统墙钟跳变时清除周期提示，重新对齐持久化的真实期限，不把进程 monotonic 数值持久化。

任务领取仍每秒检查持久化队列，Web 随时提交的 pending 任务不依赖 Worker 内存提示。P5领取通过 SKIP LOCKED 选择候选并在短事务中核对租约、可领取状态及产线互斥，无候选时不更新任务。延迟接纳常规每5秒检查，完成提交或领取释放队列容量后提前检查；无到期 deferred 或队列已满时只读返回，实际接纳在写事务中再次核对容量和 next_attempt。心跳及租约续约仍为5秒/30秒。

离线对照：以 P1 `e8605d8` 和本次代码分别使用真实临时 SQLite，两条切割线、一条焊接线、300秒刷新与告警间隔、开启当日对账，先执行一次 tick，再模拟600次一秒 tick；业务任务体替换为成功完成，不连接外部数据库。以下为 Python 方法计数和 SQLite trace 实测，不是现场吞吐/耗时或生产性能百分比。

| 600次 tick | P1 | P2 |
| --- | ---: | ---: |
| schedule | 4800 | 12 |
| due | 4800 | 12 |
| enqueue（含真实周期轮次及重复对账尝试） | 2412 | 12 |
| request_snapshot_refresh | 2400 | 4 |
| admit_deferred | 600 | 126 |
| claim_task | 1223 | 1223 |
| BEGIN IMMEDIATE | 11566 | 182 |

两版期间领取任务分布完全相同：current_refresh 4、leadership_refresh 8、alarm 4、report_snapshot_refresh 7；最终均保留28个任务、8条周期进度。未以延长业务间隔换取减少访问。定向验证另覆盖未到期零查询、入队失败回滚、跨日/逐月对账、重启恢复、旧行升级、来源和间隔热更新、计划清理、时钟跳变及 deferred 退避；未进行真实外部系统或生产负载验收。

## P3：SQL 资源准入与隔离（2026-11-03）

`core/sql_capacity.py` 在 SQL Server connect/批量/流式/多结果集及 MySQL 只读事务入口统一执行全局、来源和产线准入。多级许可全部非阻塞获取，失败释放已取许可后进入有界等待；MySQL 在池 checkout 前准入并保持到连接归还。旧 TLS 子进程复用父进程许可，POSIX 下继承锁描述符，避免重复准入。请求取消、队列满、排队期限及原总期限沿用现有取消/期限机制。

一个 Web 和一个 Worker 共享本地 `data/.coordination/sql` 固定锁槽，不向状态库写入配额。锁保障跨进程容量，进程内计数分别由管理员资源 API 和 Worker 心跳观测，不作为部署全局实时计数。来源以稳定 source_key 隔离，产线以目录中的稳定 ID 隔离；HR/PDM/派发共享查询不伪装成默认切割线。HTTP 查询执行池按来源/产线限制线程投递，快照使用独立池；SQL 在数据准备结束后释放，不覆盖后续模型与 Chromium 阶段。

容量是冷配置，统一重启 Web/Worker 生效；连接参数保留原热加载边界。单源容量不参与来源指纹，容量调整不失效 P1 的历史快照/分析或 P2 的任务来源键。具体默认值、覆盖和诊断见 [配置参考](../deployment/配置参考.md#sql-与-http-资源容量)。离线验证使用临时本地锁与驱动替身，覆盖20请求上限、来源/产线隔离、跨进程崩溃释放、排队取消及驱动生命周期；不据此推断生产吞吐或真实数据库承载能力。

补充验收：真实 Worker 持久化队列在 Web 占满同源容量时进入原 deferred 退避，释放后同一任务重试完成；自动化数据准备遵守同一容量。焊接分析/PDF 接口在慢模型/渲染阶段的 SQL 执行与等待计数均为0，其他查询仍可进行。旧 TLS 在全局/来源均为1时正常查询，证明父子不重复准入；POSIX 父进程崩溃后，真实查询子进程继承的锁持续保护在途查询，子进程退出后恢复。SQL 排队耗尽总期限计入超时统计，由 HTTP 边界明确返回504，报表路由保留取消与资源繁忙异常。

## P4：PostgreSQL 状态库准备（历史阶段，2026-11-04）

`database/postgres.py` 使用锁定psycopg3和psycopg_pool，提供独立有界池及提交/回滚/健康检查；P4阶段尚未装配到SQLite Runtime；P5已接通正式运行。状态库POSTGRES_*与生产SQL Server/MySQL Registry分离，配置均冷生效。P4的Compose postgres profile使用独立持久卷、不发布端口；P5已改为Web/Worker必需依赖；迁移与未来运行账号均非超级用户，运行角色只授予业务表DML。

独立`postgres_migrations/001_initial.sql`依据实际SQLite001–016枚举建立38张业务表、3张迁移元数据表；PostgreSQL版本按真实DDL执行记载，源SQLite迁移历史单独归档。事务advisory锁串行安装，脚本校验和和结构指纹校验拒绝漂移。BIGINT/DOUBLE/TEXT保留历史ID、整数布尔和文本/浮点时间；ASCII NOCASE通过专用表达式唯一索引保留。

`scripts/migrate_sqlite_to_postgres.py`提供schema/status/check/migrate/verify；复用现有文件锁、有效租约及SQLite写围栏，从官方一致性备份分批导入，逐表全字段规范化摘要、计数、约束、业务关联和必要实体文件核对。所有导入及成功记录同一PG事务，不覆盖未知目标，不双写、不自动切换。P4演练副本仅代表迁移时点。详细版本、完整表清单、真实PG验收及P5正式切换流程见 [迁移说明](../deployment/PostgreSQL状态库迁移.md)。


## P5：PostgreSQL 唯一状态库（2026-11-04）

Web/Worker 各复用一个进程池，所有状态仓储、UnitOfWork、租约和领取直接使用 PostgreSQL。SQLite 仅留离线历史升级和迁移，不参与正式运行，也不在故障时回退。保持单 Web/单 Worker、原外部数据源和同机容量锁。

具体事务及并发边界见 [PostgreSQL仓储与事务设计](../design/PostgreSQL仓储与事务设计.md)，首次停机迁移、账号、备份及回退见 [部署说明](../deployment/PostgreSQL状态库迁移.md)。本轮不执行生产切换，不生成部署包；P6/P7 的调度拆分和多 Worker 不在此次范围。


P5验收使用隔离的真实 PostgreSQL 18.6（本机55435、随机测试数据库），未连接生产状态库。`test_postgres_runtime.py` 的13项覆盖A–H：全部业务表历史导入与仓储读取、真实独立Worker与Web共享状态、跨仓储提交/回滚、并发领取和唯一性、跨进程租约竞争与提交围栏、辅助线程失效隔离、断库503与恢复、SQLite连接禁用。与登录容量4项合并执行17项通过；P4迁移既有12项及新增迁移标记拒绝1项通过。其他受影响的报表、确认、配置、快照、用量、调度、派发、自动化/邮件、告警和清理按调用范围分批验证，没有运行无关前端视觉测试或全量后端套件。

Compose只读解析通过必需PG健康依赖、运行/迁移账号分离、卷身份和无PG公开端口检查，启动脚本通过bash语法检查。实际生产切换、SQL Server/MySQL/能管/魔盒/SMTP/LLM连接及生产负载未在本机验证；外部业务集成用例使用已有替身，不能据此宣称现场验收完成。


## P6：Scheduler、Consumer、Automation 独立进程（2026-11-04）

现行部署和恢复边界以本节为准；上文 P1–P5 保留各阶段改造记录。`scheduler.py` 迁入 P2 的全部周期调度方法及 monotonic 到期提示，不领取或执行业务；`worker.py` 只领取/执行/提交后台任务与报表，保留4操作、10分析执行池；`automation_worker.py` 独立运行原计划扫描/补偿与邮件两个线程。`ReportWorker` 只生成报表并保留完成/失败回调，不再启动自动化或邮件线程，无切回混合后台的分支。

复用 PostgreSQL 的 `scheduler`、`worker`、`automation` 独立租约与真实角色 `execution_scope`，仍为30秒租约、5秒心跳和提交围栏。同角色互斥，不同角色可任意顺序启动。Consumer独占任务/报表/能耗恢复；Automation独占 sending→unknown 恢复，未知邮件不自动重发；Web原派发/下载恢复保持。能耗待提交结果移到Consumer，热重建不回收同owner的在途claim。快照期限从持久化完成时间加间隔计算，Consumer不修改Scheduler内存；P2未到期跳过、来源隔离与P3共享容量保持。

自动化计划复用原Service和仓储创建并关联报表，Consumer完成回调与Automation补偿共用原outbox唯一约束。SMTP/外部SQL/模型/PDF在状态事务外执行；崩溃后依持久状态恢复，不假定外部副作用可回滚。未新增队列、状态表或资源框架，仍只有单Consumer，P7多消费者未实施。

Web通过缓存2秒的持久心跳分别报告三个角色；任一异常使background_ready=false，PG不可用返回503。配置诊断分别比较四角色实际快照，公开健康递归去除内部配置指纹；诊断采集失败不阻断续约。四角色各复用独立有界PG池，默认总上限16，退出释放资源。Compose与离线流程共用一个应用镜像，仅依赖PG健康，共享本机data/reports/logs及容量锁，后台不开端口。

真实隔离PG定向覆盖三种启动顺序、逐角色强杀接管、独立互斥/围栏、周期/消费分离、outbox并发补偿、能耗热重建、配置及脱敏；原P2调度、P3跨进程容量、自动化热加载与SMTP未知状态回归已通过。Docker隔离演示卷验证三个实际入口健康、Scheduler单独重启与PG暂停/恢复，未执行生产或外部SQL/模型/SMTP现场联调。旧SQLite现场先完成P5最新数据离线迁移/校验，禁止直接启动新后台或与旧混合Worker并跑。完整启动、探针、恢复及回退见 [四角色部署说明](../deployment/WEB_WORKER进程隔离升级说明.md)。


## P7：同机多Consumer与实例级故障隔离（2026-11-04）

现行Consumer协议以本节为准；P1–P6为各阶段记录。Consumer使用worker:实例ID租约，原生显式配置CONSUMER_INSTANCE_ID，Docker使用独立容器hostname；owner仍是每次获取生成的新随机令牌。Scheduler/Automation保持单实例。实例上下文传入原有线程池和ReportWorker，PG进入/提交围栏继续检查实际租约和数据库时钟。

后台任务仍原子SKIP LOCKED领取，保留同产线同类普通任务部分唯一索引及分析分类；完成核对running、owner、claim_token和attempts。后台/报表/能耗恢复均只处理失效owner，锁候选后对owner租约取不等待共享锁，跳过未提交续约/接管，再用新语句快照复核，启动及每5秒精确恢复。报表状态/元数据校验execution_owner，文件按report_id+owner隔离；能耗保留claim_token和幂等初始化。新告警/能耗载荷带来源指纹，无指纹或错来源任务blocked，禁止使用新绑定猜测执行。

003_consumer_scaling只追加owner索引、后台claim_token和维护门槛，不改变001/002或重建任务表。旧全局worker记录须在停机且全部租约失效后由cutting_migrator的prepare-consumers显式处理；SQLite全字段导入/verify先完成，随后恢复执行态和标识不可信来源。新Runtime不运行旧协议，不在启动时全局重置。

Consumer实例各自心跳及本实例探针，WorkerStatusReader保留旧聚合字段并增加实例列表、正常/失联计数，区分running/degraded/unavailable；历史lost/stopped保留在实例列表且不拖累健康替代实例；CONSUMER_REPLICAS明确Web最小健康期望（默认1、冷生效），健康数不足报告degraded，全部失效报告unavailable，有效停滞实例仍报告异常。--scale只变更实际副本数，不推断期望。配置/资源诊断按实例展示，公开响应递归去除内部比较值。原retention清理超过30天且无运行任务引用的失效实例记录，同ID重启只更新同一租约。

本地容量默认4操作/10分析/1报表，每实例独立有界PG池默认0–4，应用连接预算4×(N+3)另加探针余量。所有进程继续共享P3的本机SQL/模型/PDF文件锁，慢外部工作不占PG事务；不支持跨主机。Compose去掉Worker固定名，CONSUMER_REPLICAS默认1；同一镜像及离线卷支持1→3→1，不重启其他角色。停止时先停领取，仅在执行线程全部停止及完成提交后释放租约，否则由TTL/围栏接管。

验证包括真实PG所有权/恢复/事务、真实Consumer并发与强杀、完整设备报表崩溃后同ID新owner文件发布，以及Docker实际四角色探针与1→3→1。受控9任务的1/2/3进程实测及SQL/模型/PDF上限见[部署与验证说明](../deployment/WEB_WORKER进程隔离升级说明.md#验证与回退)。这不是生产性能或现场迁移验收；后续只进行授权的迁移、真实源/SMTP/模型/能管联调及必要修复。

## P1～P7代码收尾边界（2026-11-04）

P1～P7核心开发完成。本轮只修复CI测试装配、类型夹具及历史Consumer健康误判，保留统一产线、P2调度、P3配额、PG迁移/状态库和四角色职责与领取/恢复协议，不新增架构层。CI最终以修复提交关联的Frontend CI和API contracts全部通过为准，结果记录在[项目进度](progress.md)。生产PostgreSQL历史数据迁移尚未执行；SQL Server/MySQL、SMTP、LLM等现场联调、生产部署与实际负载验收均未完成，不能将代码验证视为生产上线。
