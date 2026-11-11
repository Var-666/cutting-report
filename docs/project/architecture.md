# 当前系统架构

本文件描述现行实现。开发约束见[AGENTS.md](../../AGENTS.md)，业务字段与统计口径见[功能与接口参考](功能与接口参考.md)，运维操作从[文档入口](../README.md)选择。

## 总体结构

```text
Vue 3 / Soybean Admin / Naive UI
  ↓ 同源HTTP、Cookie会话、明确的设备/产线上下文
FastAPI API → Service → Repository / 外部客户端
                        ├─ PostgreSQL：应用状态、任务、快照、确认与审计
                        ├─ SQL Server：切割生产、HR、PDM、可选BM
                        ├─ MySQL：焊接生产
                        └─ 能管、魔盒、SMTP、LLM及受控文件
```

外部生产库只读；PostgreSQL是唯一应用自有状态库。旧SQLite升级/导入代码已退役，正式运行及维护不访问SQLite。报告与下载实体放同机共享文件卷，元数据和处理状态保存在PostgreSQL。

## P01 后端边界与后续治理（2026-11-02）

### 职责与依赖

- `api/`负责HTTP输入、角色权限、请求容量和状态映射；业务规则放`services/`，SQL及原子约束放`database/`。默认调用方向为`api/workers → services → database`。
- `models/`保存领域与HTTP契约，`core/`保存共享取消注册表、执行期限、进程锁和SQL容量，不放业务字段或服务定位器。
- `main.py`、各后台入口、`application_runtime.py`和`device_runtime.py`是装配边界；允许连接Service、Repository和Worker。`services/frontend_static.py`仅供Web提供静态资源。
- 取消、期限、进程锁统一从`app.core`导入。条件模型/校验在`models/conditions.py`，字段映射与SQL/Mock编译在`database/condition_fields.py`和`condition_query.py`；`condition_service.py`只提供公开schema。
- 切割筛选/最近生产日SQL位于`database/queries/`，由`cutting_queries.py`执行；HR与魔盒配置查询分别归`database/hr.py`、`magic_box.py`。焊接SQL和模型等待登记分别归已有焊接、协调仓储，Service保留业务编排。
- 确认归属、派发唯一性、告警归并/认领、租约和队列状态等原子约束留在同一仓储事务内；不为了分层拆成先检查后写入。

### 兼容边界

`WorkshopRepository`门面、嵌入式平铺state、旧线程/调用签名仍有实际调用方；正式入口使用类型化Runtime和独立仓储。新增功能不扩散兼容分支；删除前检查源码、脚本及测试。不合并职责不同的Runtime、Registry、Provider、缓存和快照，也不主动重写剩余源码提取测试。

## Runtime与配置

- `application_runtime.build_runtime(settings, role=...)`装配各角色共享资源，`build_device_runtime`装配切割业务。所有产线实例统一登记在`DatabaseServices.lines`。
- Web只挂载`app.state.runtime`。API通过`application_state`获取共享资源，`runtime_state`固定单请求的数据库服务快照，`device_state`校验唯一有效的`device_id`；Worker在新操作边界固定快照。在途操作继续使用原服务引用。
- `DatabaseRuntime`和`AutomationRuntime`管理不可变配置快照。保存复用进程锁、锁内读改写、空密码保留、候选校验及原子文件发布；失败保留旧快照，不关闭仍被在途请求引用的连接池。
- 配置解析由`configuration_sources`统一维护，优先级为`data/settings.env > 进程环境 > .env > 默认值`；业务使用传入Settings，不另读环境。
- 产线目录、连接、自动化/SMTP及模型 ID 按现有白名单热加载。SQL容量、PG连接和其他冷参数按[配置参考](../deployment/配置参考.md)重启生效。只读诊断使用`inspect()`，不触发配置发布；比较四角色实际快照，不把目标值冒充已生效值。

## 产线、设备与权限

- `ProductionLineCatalog`从`PRODUCTION_LINES_JSON`校验稳定ID、类型、物理设备范围和`source_key`；外部连接只从`DATABASE_SOURCES_JSON`进入`DatabaseSourceRegistry`。
- 同类型新实例通过配置接入；新类型登记构建函数、能力及对应业务入口，不复制公共Runtime/Worker/API。Provider统一访问与刷新契约，各产线保留自身指标、日期和单位。
- 切割API必须显式提供唯一有效的`device_id`，焊接API明确提供`X-Production-Line`。产线头只限制能力，不能代替管理员/工人授权；前后端权限一致。
- 任务确认以`(scope_machine_no, task_id)`隔离；日报按设备和日期保存，队列、导出、缓存、告警和分析继续保留设备/来源身份。不得凭当前默认设备猜历史归属。
- BM船名通过可选`bm`数据源批量精确匹配完整工程号，字段缺失/冲突保留任务。工程统计键不变，确认/撤销不依赖BM可用性；PDM/派发船号保留原语义。
- SQL Server旧TLS只对显式启用`legacy_tls`的数据源批量只读查询使用一次性子进程；凭据走私有管道，取消/期限继续回收资源，不更改其他连接的TLS策略，不支持直接连接和流式接口。

## 后台角色、队列与恢复

| 角色 | 数量与职责 |
| --- | --- |
| Web | 单实例，同源静态资源、API、鉴权和任务提交；保留下载/派发恢复 |
| Scheduler | 单实例，生产周期任务与每日对账入队，不执行业务任务 |
| Consumer | 默认1个，可同机扩至N个；领取后台/报表任务及恢复报表、能耗状态 |
| Automation | 单实例，计划扫描、补偿、SMTP消费及发送未知状态恢复 |

四角色共享同一PG及本机文件卷，各复用一个有界PG池，不支持跨主机或网络共享卷。Web不启动后台消费者，不用多个Uvicorn workers替代Worker。

Scheduler保留有界monotonic到期提示，PG中的计划/队列决定真实期限与去重；持久化失败不推进提示，来源/计划变化按新快照处理。Consumer每秒领取、5秒心跳，延迟任务按原退避和容量重新准入。

后台使用30秒租约、5秒心跳和真实角色提交围栏。Consumer使用`worker:实例ID`租约，实例ID与每次获得的owner token分开；原生必须显式配置唯一`CONSUMER_INSTANCE_ID`，Docker使用容器身份。任务通过`SKIP LOCKED`原子领取，完成校验owner、claim_token和attempts，报表及能耗保留各自所有权检查。

恢复只处理失效owner，不重置其他正常实例；来源缺失/不匹配的旧任务标为blocked，不猜测当前绑定。SMTP和派发结果未知时不盲目重试；数据库回滚不代表外部副作用撤销。

Web健康检查区分Web/PG可用性与`background_ready`；后台状态读取持久心跳，Consumer按`CONSUMER_REPLICAS`最小期望容量汇总。历史lost/stopped实例不降低健康容量，当前停滞及数量不足仍报告异常。配置诊断仅展示当前运行/停滞Consumer；超过30天且无恢复引用的失效记录由原清理任务删除。

启动、扩缩容、探针及回退见[后台部署说明](../deployment/WEB_WORKER进程隔离升级说明.md)。

## 连接、事务与资源

- PostgreSQL连接、事务、租约上下文及提交围栏统一在`database/postgres.py`；跨仓储原子操作复用`UnitOfWork`。SQL/HTTP/模型/SMTP/PDF/文件等慢操作不进入状态写事务。
- Schema通过`postgres_migrations/`追加，普通启动不建表、不迁移。详见[事务设计](../design/PostgreSQL仓储与事务设计.md)和[迁移说明](../deployment/PostgreSQL状态库迁移.md)。
- SQL Server/MySQL驱动统一通过`core/sql_capacity.py`获取全局、来源和产线配额；四角色共享本机固定文件锁槽。等待不持有部分配额，不向PG写配额状态。HTTP、SQL、LLM、Chromium容量分别管理，资源计数是进程本地观测。
- HR缓存命中免鉴权执行池；必要HR复核、本地密码校验及账号创建仍有界。同步操作结束才释放请求名额，取消不能提前放宽实际并发；会话/角色校验不因繁忙跳过。

## 报表、缓存与智能分析

- 页面、HTML/PDF导出与分析共用统计口径、时区和区间。管理员车间/设备报表默认当天，无数据展示空状态，不回退到旧生产日；工人任务日期规则独立保留。
- 日报同步更新已有任务的生产字段并追加新任务，保留日报/任务身份、确认及审计；不因字段变化拒绝整页。字段修订及旧页面确认保护见[功能参考](功能与接口参考.md#账号与车间确认)。
- 查询复用有界`QueryCache`，跨进程失效复用`CacheRevision`，持久快照复用`ReportSnapshotService/ReportKey`及原队列。切割领导月报与能耗保留专属快照，焊接聚合和分页快照规则见[缓存与快照](multi-line-report-data.md)。
- 前端保留当前设备的领导、车间、设备报表，返回时先展示匹配条件的已有数据并静默更新；跨日期/设备不混用，首次无缓存仍需加载。失活取消轮询和在途订阅，重新激活不叠加监听。
- `services/report_analysis/`按三类切割报表构建权威事实、候选及完整业务关联，模型仅接收带覆盖说明的必要样本和汇总；`common.py`共享规范化和业务版本算法；`ReportAnalysisService`处理源版本、缓存/单飞、期限、模型及持久化。
- 提示词和版本统一在`services/llm_prompts.py`。`llm_service.py`保留请求、限流、重试、取消/期限、用量和JSON校验；焊接与告警保留独立单位及输出契约。
- 切割分析新增可选 `brief`，模型只返回结论、事实ID、候选发现和跟进动作；后端校验引用、数字/日期及变化方向，再绑定原产线/周期内的真实业务对象。完整关联保存在既有分析JSON中，前端预览3项并按页查看全部；任务通过车间区间接口的精确 `task_id` 筛选定位。旧四字段由确定性适配保留，历史JSON无需迁移；焊接四段契约及告警专用契约独立保留。输入、输出和示例见[智能分析依据](功能与接口参考.md#智能分析依据)。
- `services/report_generation/`按领导、车间、设备和自定义报表准备内容，显式接收协作服务。`ReportPipeline`统一校验设备、取消/超时和降级，渲染后在一个UnitOfWork保存报告及完成状态。
- 生产数据刷新与模型执行分开；默认页先读同设备/周期成功分析，后台按配置更新，失败保留已有结果。分析版本冲突、模型错误及导出现场校验继续生效，不能用旧快照绕过业务校验。

## 前端与文件生命周期

Vue 3使用Soybean Admin、Naive UI、Pinia和UnoCSS。主题经现有store与`theme/reportVariables.ts`桥接，切换主题不触发业务查询。业务API复用同源Cookie、设备/产线、请求取消、缓存和会话恢复；生成类型统一来自OpenAPI。

表格、查询、指标及图表复用已有公共组件，单行、自适应360px文本上限、铺满容器、拖动与悬停全文等约束统一维护在[前端规范](../../AGENTS.md#前端设计开发规范)。

文件派发的批量弹窗与专属样式由`DispatchBatchDialog`维护，派发执行与重试仍由`useCuttingDispatchBatch`协调。能耗报表的日期、路由、请求取消与激活恢复集中在`useEnergyReportQuery`，月汇总同步与有界轮询由`useEnergyReportSummary`维护；页面保留展示、导出和图表DOM生命周期。车间已有确认/更正弹窗及相关逻辑继续复用，不按行数强行拆分。

入口HTML使用`Cache-Control: no-store`，Vite哈希资源长期immutable缓存。报告、文件、任务和审计按现有保留策略清理，活动记录及待发送邮件保留；文件删除通过持久队列重试，受控路径不可越界。
