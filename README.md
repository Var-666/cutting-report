# 切割生产智能产报系统

基于 Vue 3、FastAPI、SQL Server、ECharts 和 OpenAI-Compatible LLM API 的内网生产报告系统。

项目说明材料统一收录在 [`docs/`](docs/README.md)，Windows 部署请直接查看 [`docs/deployment/WINDOWS部署说明.md`](docs/deployment/WINDOWS部署说明.md)。

当前版本已接入项目根目录的 `TASK.xlsx`，数据库暂时不可连接时可直接使用导出的完整任务表：

```text
Vue 查询条件
→ FastAPI 创建任务
→ SQLite 持久化状态
→ 后台线程生成报告
→ Python 统计
→ ECharts 图表
→ LLM 可选分析/失败降级
→ 单文件 HTML
→ 历史列表、打开、下载
```

系统顶部提供三个可扩展功能区：

- **报表**：内含领导报表、车间报表、设备报表和生成与历史四个子页面；
- **数据库管理**：检查数据源状态、测试 SQL Server 连接、检查三类 SQL 映射并预览固定数据集；
- **报表设计**：维护查询任务、报表定义和可复用模板。

前端使用 Vue Router 和角色守卫，管理员路由位于 `/admin/*`，工人任务确认位于 `/worker/tasks`。页面支持刷新、前进、后退和直接访问，无权路由会自动返回当前角色首页。

数据库管理界面不会保存连接测试密码；SQL 工作台只允许只读查询。

数据库管理页还提供受控的 **SQL 工作台**：

- T-SQL 编辑、格式化和语法校验；
- 只允许单条 `SELECT` 或 `WITH ... SELECT`；
- 禁止写入、DDL、`EXEC`、`WAITFOR`、`OPENROWSET` 等危险结构；
- 限制查询超时和最大返回行数；
- SQL Server 模式使用服务器配置的只读账号，并要求独立管理员令牌；
- SQL 正文不写入普通业务日志。

领导、车间和设备报表已按 SQL Server 的 `dbo.task` 表完成查询映射。生产数据源只支持 SQL Server；Excel 联调模式已移除，演示模式仅用于隔离测试。

生产模式配置：

```env
APP_DATA_SOURCE_MODE=sqlserver
```

同时填写 SQL Server 只读连接参数。旧配置中的 `APP_DATA_SOURCE_MODE=excel` 必须改为 `sqlserver`，`TASK_EXCEL_PATH` 不再使用；不会自动回退到演示数据。历史报告、快照和原始 Excel 文件不删除。

领导报表统计口径：仅统计 `STATUS = 2` 且 `WORK_TIME >= 0` 的任务，负工时异常任务整条剔除；优先按 `OP_DATE` 归属日期，无法转换时回退 `START_TIME`；切割和坡口长度由毫米换算为米；`WORK_TIME` 按秒汇总；历史对比覆盖所选月份及其前十一个月；图表时间轴从左到右递增；不按 `PORTION_ID` 去重。

领导报表使用“内存缓存 + 应用 SQLite 月快照”，页面优先显示最近一次成功快照，过期数据由独立后台线程更新；当前月默认每 300 秒刷新，最近三个月每天校准，其他历史月在被查询且过期时刷新，首次启动会预热最近十三个月。普通月份查询不会绕过缓存，“刷新最新数据”会提交后台刷新且不阻塞页面。SQL Server 查询在一个连接内使用会话临时表完成每日和工程聚合，不修改生产数据库表。当前周期三类标准报表在 SQL Server 模式下每 300 秒后台刷新；已启用大模型时，每轮强制重新调用对应模型，即使数据未变；上一轮未完成时新一轮仍独立并行，普通页面查询继续读取缓存。间隔由 `REPORT_BACKGROUND_REFRESH_SECONDS` 控制；页面查询缓存仍由 `LEADERSHIP_REPORT_CACHE_SECONDS`、`REPORT_DATA_CACHE_SECONDS` 控制。

三类导出报告：

管理员登录后默认进入独立的生产管理首页，首页展示本月核心指标、三类报表入口、最近生成记录和管理快捷入口；“生成与历史”仅作为报表栏目下的功能页保留。

登录成功或恢复登录状态后，后台并行预加载本月领导报表、最近生产日车间报表、今日设备任务能耗及本月已保存的每日能耗，以及报告历史第一页。进入页面复用同一请求和短期结果，不等待全部查询完成才进入首页；首页显示各项加载状态，失败项进入页面可重试。工人账号只预加载车间任务。前端缓存仅存在当前会话内存中，退出或切换账号即清空；手动刷新绕过缓存，工人确认状态仍每 5 秒重新查询。车间快照不存在时仅提前读取源任务（最多缓存 60 秒），进入页面才按原流程创建日报，绝不自动确认任务。预加载不执行文件检索/下载/推送、月度批量同步、HTML 生成或大模型分析。

- 领导报表支持自定义起止日期，包含区间汇总、每日长度/工时趋势和按月汇总；
- 车间报表支持自定义起止日期，包含生产指标、任务切割长度/坡口长度/工时组合图、任务明细和工人确认统计；管理员区间查询不会为各日额外创建车间快照；
- 设备报表支持自定义起止日期，同页展示区间任务能耗和按日汇总；SQL Server 模式通过能管接口按任务实际起止时间计算气耗、电耗。未配置、未绑定、缺数或接口失败时显示 `--`，不回退到模拟值。只有显式 `demo` 模式生成标注清楚的演示能耗，不能用于结算或考核。

三类内置报表的自定义区间均为闭区间（包含开始日和结束日）。领导和车间报表最长 366 天；设备任务能耗涉及能管平台实时采集值，自定义区间最长 31 天，近 12 个月趋势使用默认月度汇总接口。页面查询和 HTML/PDF 导出使用同一区间，导出文件名及“生成与历史”会保留完整起止日期。区间智能分析使用紧邻的前一个等长区间作为对比。
统计区间在三张报表中复用同一弹出式选择器，提供单日、最近 7 天、跨月近 30 天、本月、上月、本季度和上季度快捷项；点击“应用”只更新条件，仍需点击“查询报表”才读取数据。
- 三类报表均包含报表专用智能分析；未配置模型时明确提示不可用，不用规则结果代替。当前周期由后台每轮强制重析；自定义区间仍按需查询与分析。

设备报表拥有独立展示页，进入后默认定位最近一个有数据的生产日期，也可改为任意区间；任务明细按“序号、工程号、船号、分段名、开始时间、切割时长、电耗、气耗”展示。

设备页面和导出文件的图表只使用上述明细表可见字段：以开始时间为横轴，展示切割时长、气耗和电耗；不使用隐藏的设备编号、切割长度或任务数生成额外图表。

### 能管电耗 / 气耗接入

#### 统一设备能耗报表（无需设备绑定）

“设备报表”不再拆分为两个切换页面，同一页面固定显示两张表。任务表从 `TASK.xlsx`
或生产数据库读取任务实际开始、结束时间，并直接使用用户确认的 DH01、DH02、DH03
电表合计及累计气量参数计算，不依赖 `MACHINE_NO` 绑定。

- 当日任务表每个任务一行，末行为当日任务汇总，左侧显示任务电耗/气耗双轴图。
- 月度表包含所选自然月的全部日期（支持闰年），每个日期一行，末行为当月汇总，
  左侧显示逐日双轴图。
- 任务能耗严格使用每项任务的实际 `START_TIME` / `END_TIME`；任务时间重叠时不重复
  分配同一仪表耗量，相关行保留缺失及原因。
- 查询单日后，将小时明细、读数边界依据和日合计保存到本地 SQLite
  `energy_daily_snapshots`。不保存能管密码或授权码，也不修改能管平台数据。
- “查看月份”读取已保存记录；“同步本月”依次查询未保存日期及今日；
  “重新查询本月”重新查询全部已到日期。同步有进度、可以停止；已完成日期不会丢失。
  当前最新生产日还会由后台定时更新；历史日期仍按需查询与保存，历史记录不会在月底自动删除。
- 小时区间严格使用整点至下一整点；全天为本地时区 00:00 至次日 00:00。
  不能将当天第一条、最后一条读数直接当作日界。今日只统计已结束小时，未结束小时和未来日期不填零。
- 合计只加有效小时/日期；缺失保持 `--`，页面展示实际有数据的小时数和日期数。
  所选电表任意一块缺数时，该小时电耗整体缺失；非精确边界按配置规则进行插值估算，不在数值前添加符号。
- 统一任务能耗和日/月报的历史阈值为 `ENERGY_REPORT_MAX_GAP_SECONDS=1800`，
  旧设备任务接口的历史阈值为 `ENERGY_MAX_GAP_SECONDS=300`。
  按已确认日期，默认从 `ENERGY_SAMPLING_CHANGE_AT=2026-09-04T00:00:00+08:00`
  起，两条计算路径统一使用 `ENERGY_NEW_MAX_GAP_SECONDS=180`：连续超过3分钟无读数的
  区间不计入有效耗量，保留缺口原因，不填零。允许正常一分钟采样的轻微抖动。
  跨切换点的相邻采样分别校验切换前后的间隔；插值规则、单位换算和60秒接口缓存不变。
  切换时间可配置，无时区时按 `APP_TIMEZONE` 解释，显式留空关闭新规则。
- 切换前的完整日期直接沿用原快照；受新规则影响的日期使用独立快照，旧记录不删除。
  旧结果未重新校验前不计入新规则的月合计，页面提示待重新同步的天数。
  点击“同步本月”可补齐；新查询不会因新规则认定缺数而退回旧规则的有效数值。
- 电耗单位统一使用 `kWh`（1 kWh = 1 度电），气量单位使用 `Nm³`。
- 保存记录按接口地址、账号标识、参数组、单位、换算因子、时区和计算规则隔离。
  修改这些配置后不会混用旧口径记录。网络/认证失败，或有效时段减少时，不覆盖此前已保存的较完整记录，并提示保留原因。

启动真实查询最少需在内网部署 `.env` 配置 `ENERGY_API_ENABLED=true`、
`ENERGY_API_BASE_URL`、`ENERGY_API_USERNAME`、`ENERGY_API_PASSWORD`；
电表组默认值见 `.env.example` 的 `ENERGY_REPORT_ELECTRICITY_PARAMETER_IDS`，
气表仍使用 `ENERGY_GAS_PARAMETER_ID`。统一设备报表通过
`ENERGY_REPORT_EQUIPMENT_IDS=Cutter01` 只统计 Cutter01；Cutter02 尚未配置，不能加入同一仪表组。
旧的 `ENERGY_EQUIPMENT_IDS` 不控制这张统一设备报表。

接口：管理员会话下 `GET /api/v1/energy-report/tasks?report_date=YYYY-MM-DD&refresh=true`
查询任务能耗；`GET /api/v1/energy-report/day?report_date=YYYY-MM-DD&refresh=true` 查询并保存
全天内部统计；`GET /api/v1/energy-report/month?report_month=YYYY-MM` 读取整月记录。设备页面的近 12 个月汇总优先返回已保存快照，缺失历史月由后台线程按周分片从能管平台补齐，页面自动更新，不阻塞登录和首屏。

#### 按任务能耗报表

网页、HTML 导出和智能分析统一使用 `EquipmentEnergyService`。气量单位已确认为 **Nm³**。
密码、CompanyId、AuthId 不进入前端、日志或导出文件；接口只使用登录和采集值查询，不调用 `SaveCollectValue`。

在能够访问内网 `10.147.0.38:10091` 的部署机器 `.env` 中配置下列字段，修改后重启后端：

```env
ENERGY_API_ENABLED=true
ENERGY_API_BASE_URL=http://10.147.0.38:10091
ENERGY_API_USERNAME=填写能管账号
ENERGY_API_PASSWORD=填写当前有效密码
# 先确认是 Cutter01 还是 Cutter02；不可把同一套仪表绑定给两台物理设备。
ENERGY_EQUIPMENT_IDS=填写2万瓦设备的实际MACHINE_NO
ENERGY_GAS_PARAMETER_ID=bd3179be-c587-f011-a2e9-000c2974f33c
ENERGY_GAS_UNIT=Nm³
# 仅填确认需要相加的电表参数ID，逗号分隔；总表和分表不能重复计量。
ENERGY_ELECTRICITY_PARAMETER_IDS=填写经确认的参数ID列表
# 确认电能原始单位/转换因子后填 kWh。
ENERGY_ELECTRICITY_UNIT=kWh
ENERGY_GAS_FACTOR=1
ENERGY_ELECTRICITY_FACTOR=1
ENERGY_ALLOW_INTERPOLATION=true
ENERGY_MAX_GAP_SECONDS=300
ENERGY_CACHE_SECONDS=60
ENERGY_API_TIMEOUT_SECONDS=10
```

仪表配置表提供的正向有功电能参数：

| 电表 | 参数编号（不是仪表编号） |
| --- | --- |
| DH01 | `2b00e015-d987-f011-a2e9-000c2974f33c` |
| DH02 | `98cbd135-d987-f011-a2e9-000c2974f33c` |
| DH03 | `93fae152-d987-f011-a2e9-000c2974f33c` |

采集接口返回 `MfId / Mt / Mv`。客户端兼容数组、JSON 字符串和 `Content` 包装，登录自动获取 CompanyId/AuthId，鉴权过期最多重登一次；网络失败短暂退避，页面、导出与分析共享短期读数缓存。查询按最多 24 小时拆分，不同时间片只在各自的锁内合并重复请求，不会用一次长区间查询阻塞所有能耗请求；系统不长期保存原始采集数据。

计算及质量口径：

- 必须精确匹配 `MACHINE_NO`；可配置同一设备的历史别名，但不能关联多台不同设备。
- 气耗 = 累计气量结束读数 − 开始读数；电耗 = 已确认的电表各自正向有功电能差值之和。任一所选电表缺数时，该任务电耗整体缺失，不伪造少表的合计值。
- 使用任务 `START_TIME` 和 **实际 `END_TIME`**，支持跨午夜；不以 `START_TIME + WORK_TIME` 冒充结束时刻。无效时间或同一计量设备在查询任务集中存在重叠时，不分摊、不重复计量。跨报表日的重叠任务仍需现场核对。
- 非采样时刻的起止边界用相邻读数线性插值估算；数值列不添加额外符号。所有区间内相邻采样间隔都必须在配置上限内。设 `ENERGY_ALLOW_INTERPOLATION=false` 时仅接受精确边界读数，不外推。采样缺口、回零/倒退、负值、非有限数、同刻冲突均保留缺失及原因。
- 文档注明上传的 `Mv` 已乘读数因子，默认转换因子为 1，不重复乘 CT/PT。若实际返回单位不是目标单位，必须经确认后设置转换因子；不从“2万瓦”额定功率反推用电量。
- KPI 是**已匹配任务时间区间耗量**（含该区间内待机），不是全天总用量或纯切割净耗；分别展示气、电有效记录覆盖数。空值不作为零，真实零耗量可正常显示。插值值和缺失不可用于结算或直接判定能耗异常。

内网只读联调（Windows CMD，已安装后端依赖）：

```bat
.venv\Scripts\python.exe scripts\check_energy.py --date 2026-08-31
```

未配置账号时会询问账号，密码采用隐藏输入；成功后仅显示四个参数的采样条数和首末累计读数，不显示授权码，也不把首末值差误称全天用量。可追加 `--output data\energy_probe.json` 导出原始读数用于校验（文件必须不存在）。生产数据日期必须落在仪表实际有采样的时间内，不能将当天读数补给旧报表。接口使用明文 HTTP GET 传密码，建议由内网 HTTPS 网关保护，勿使用 `curl -v` 分享包含密码的请求日志。

三类内置报表均可在生成前选择柱线组合、柱状图、折线图或面积图。领导报表的页面图表会立即切换，所选类型同时写入导出的 HTML；车间和设备报表在导出时应用所选类型。自定义报表定义也支持这四种图形。

导出的 HTML 已内置 A4 横向打印样式。使用浏览器“打印 → 另存为 PDF”时，图表会在打印前重新适配页面宽度，明细表按页拆分并在每页重复表头，避免横向溢出、列宽漂移和跨页断行。历史 HTML 文件不会自动套用新模板，需要重新生成报告。

车间任务的“工程号”和“船号”均原样显示 `PROJECT_NAME`。每个生产日期只能生成一份车间报表：系统先把当次 SQL/`task` 模拟查询结果写入 `workshop_reports` 和 `workshop_report_items`，分页和筛选只影响展示，不会重新查询或改变报表明细。所有工人均可查看整份报表。

人工确认以 `TASK_ID` 为唯一任务键，表示“这个任务是我完成的”，一个任务只能由一个登录账号确认。当前确认保存在 `workshop_confirmations`，确认、工人撤销、管理员撤销和改派写入 `workshop_confirmation_audits`，均只修改系统 SQLite，不修改生产库 `dbo.task`。车间 HTML/PDF 导出使用同一份日报快照，生成时会固化当时的确认人；之后变更确认需重新执行导出。

## 账号与车间确认

系统首次启动且管理员账号表为空时，登录页会要求创建首个本地管理员账号。启用工人数据库认证后，工人使用公司工号和 HR 数据库中的密码登录，管理员仍使用本系统账号登录。任意已登录员工首次进入当日页面时，系统会幂等地生成或读取当日唯一报表；页面每 5 秒静默同步一次其他员工的确认结果。点击“确认是我完成”后，系统立即记录当前账号姓名和确认时间，不允许第二个人覆盖。工人可在“撤销我的确认”中填写原因后撤销本人记录；管理员可将已确认任务恢复为待确认，或改派给已在系统登录过的可用工人。撤销和改派原因均为必填项。

工人认证读取 SQL Server 的 `dbo.HRbase_userInfo`，字段映射为：`公司工号`是登录账号、`密码`在 SQL Server 内直接比较、`_Identify`是稳定人员标识、`姓名`是显示姓名，同时读取`集团工号`和`可用`。不读取或校验 `_Locked`，无论它是 NULL、0 还是 1，都不影响本系统登录；公司工号唯一性、密码、可用状态和人员信息完整性校验仍保留。明文密码不会被查询返回、写入本地 SQLite、Session 或普通日志。工人首次登录成功时，系统只在 SQLite 创建一条不含密码的影子身份，用于会话和任务确认外键。

外网开发环境无需连接 HR 数据库，可保持：

```env
WORKER_DATABASE_AUTH_ENABLED=false
```

部署到能访问内网数据库的服务器后，为 HR 库配置独立连接并启用：

```env
WORKER_SQLSERVER_HOST=10.0.0.30
WORKER_SQLSERVER_PORT=1433
WORKER_SQLSERVER_DATABASE=HR数据库名
WORKER_SQLSERVER_USERNAME=HR只读账号
WORKER_SQLSERVER_PASSWORD=HR只读密码
WORKER_DATABASE_AUTH_ENABLED=true
WORKER_DATABASE_AVAILABLE_VALUE=1
```

`WORKER_DATABASE_AVAILABLE_VALUE` 必须与正式表中允许登录的“可用”值一致。工号不存在、密码错误、账号不可用或公司工号重复时，对外统一提示“公司工号或密码错误”。数据库连接失败时返回“工人登录数据库暂时不可用”。旧配置 `WORKER_DATABASE_UNLOCKED_VALUE` 已不再读取，保留也不会影响登录，无需修改 HR 原表记录。此变更需更新应用代码/容器镜像后生效，仅重启旧版本不会改变登录规则。

切割文件库使用 `FILE_SQLSERVER_*` 独立配置，关联为 `PDM_DesignDocumentInfo.ID = PDM_DocumentFileInfo.DocumentID`，并严格匹配版本。真实文件的 `FilePath` 作为 FO HTTP 下载接口的相对路径，需在现场核对一条文档及版本记录。PDM 只读连接配置完成后可启用 `FILE_DATABASE_ENABLED=true`；下载与魔盒接入相互独立。三套库的只读账号和连接参数可在“设置 → 数据库配置”中维护；密码只能更新、不会回显。配置保存到 `data/settings.env`，重启服务后生效。

文件页面按项目和 PDM 目录树逐级定位文件，分页返回所选目录的文件、保存到系统、选择系统已保存文件，以及上传魔盒并指派机床。
“文件派发”作为父级目录，分为“文件下载”和“文件推送”：

- 文件下载：`/admin/cutting-dispatch/downloads`，左侧显示项目和多级 PDM 目录树，右侧显示当前目录文件并支持文件名快速查找、逐行保存和系统保存记录，不提供下载到用户电脑的入口，也不显示派发参数和历史。
- 文件推送：`/admin/cutting-dispatch/push`，不提供条件检索；只从系统已保存且通过校验的文件中选择，填写机床及加工参数、保存草稿、明确确认推送和查看分阶段记录。
- 确认推送会复用经过大小及 SHA-256 校验的准确版本，本地不存在时先从 FO 下载，再调用魔盒上传和指派接口。`taskName` 与 multipart 文件名均使用实际上传文件的文件名（含扩展名）。
- 上传、指派独立落库；指派失败重试时不重复上传。上传超时或服务在上传阶段重启时会标记“上传结果待核对”，禁止自动重复上传。
- 原 `/admin/cutting-dispatch` 地址及同名路由自动进入文件下载，子页面也支持直接访问、刷新；仍需管理员权限。

`FILE_DATABASE_ENABLED=false` 时清单为明确标识的模拟PDM数据；默认 `FILE_SOURCE_MODE=disabled`。
真实 HTTP 下载必须同时启用真实 PDM，不能用模拟记录请求真实文件服务；模拟 PDM/文件也不允许推送真实魔盒。保存草稿不会下载、上传或回写 PDM。

#### PDM 目录树与文件查询

下载页依次调用项目、目录和文件接口，页面不会把整库文件一次加载到浏览器：

```text
GET /api/v1/cutting/projects
→ GET /api/v1/cutting/catalogs?project_id=...
→ GET /api/v1/cutting/files?project_id=...&catalog_id=...
```

- 项目来自 `BasicConfig_ProjectInfo`，显示项目名称和船号；下载页首次进入默认只查询“君正25900-9SS”，也可手动改为其他项目名称或船号，每次最多返回20个匹配项目。
- 目录来自 `PDM_NCDocumentCatalog`，通过 `ID / ParentID` 递归展示任意层级，不写死“船体、分段、板材、Gen指令”等名称。
- PDM 中与项目同名的目录根节点会在页面隐藏，避免项目名称重复显示；其子目录保持原始排序和层级。
- 点击任一目录后只查询该目录直接关联的 `PDM_DesignDocumentInfo`，右侧按文件名、中间产品、文件格式、版本、操作人、工号和操作时间分页显示，每页20条；可在当前目录内按文件名或中间产品快速查找。
- 每次下载仍绑定具体文档、版本和 `PDM_DocumentFileInfo.FilePath`；同版本存在多条历史物理记录时使用查询稳定选出的最新记录，路径无效时拒绝下载。
- “系统已保存文件”改用与来源文件一致的 PDM 目录树检索，左侧按“船体 → 分段 → 板材/型材 → 文件分类”逐级展开，右侧只分页显示所选目录的保存记录，并支持在当前目录内按文件名查找。目录ID、项目ID和文件名仍通过服务端受控字段参数化查询，不接受浏览器提供的 SQL、表名或列名。
- 下载时把项目名称、船号、中间产品、目录ID和从项目到文件分类的完整目录链写入本地记录。下载页的“系统已保存文件”默认展示当前项目全部记录，左侧目录用于按需筛选；推送页以表格列出最近100条有效、非模拟的本地下载记录，支持按项目/船号、目录路径、文件名或来源路径筛选，每页20条，并在页面重新显示时自动刷新。版本和下载人仅在结果中展示，不作为筛选条件。点击一行后才将该文件带入右侧派发参数。升级前的历史记录仍可按原字段查询，项目和目录显示为“未分类”。

本次的魔盒推送仅由管理员对单个文件明确确认触发，尚未实现自动推送或条件收藏，也不回写 PDM 状态。

#### 文件保存与选择

- 查询结果每行直接提供“保存到系统”按钮，文件名不附加版本文字，版本独立显示。下方“系统已保存文件”使用相同目录树检索，每页20条；保存完成后自动刷新当前所选目录的记录列表。
- 点击保存后，后端按文档ID重新查询 PDM，将版本和路径与页面选择核对，再读取接口。找不到对应版本或列表中的版本、路径已过期时明确报错，不回退其他版本；同版本的历史物理记录不影响重复保存。
- 接口为 `GET /FOWebApi/api/FO/FilePreView`，两个 Query 参数为 `filepath` 和 `contentType=application/octet-stream`；不需 SMB/FTP。
- 使用数据库 `DocumentNo` 作为下载文件名，不拼接版本号；名称缺少扩展名时补用 `FilePath` 的原扩展名，并清理 Windows 不支持的字符。
- 每次保存使用独立目录 `data/cutting-downloads/<下载ID>/<文件名>`，记录进入 SQLite `cutting_downloads`，包含名称、版本、大小、SHA-256、时间和操作人。同名或重试不会覆盖已完成文件。
- Docker 中的文件保存在部署服务器数据卷 `/app/data/cutting-downloads`，供魔盒推送使用。系统不向浏览器提供下载到用户电脑的接口。
- 各页面表格采用按当前内容自动分配列宽的布局，不固定列宽比例、不均分列宽。短字段、时间和数值完整显示，长文件名及说明允许换行，窄屏仅在表格区域横向滚动；分页后新内容自动参与布局，不需要手动调整列宽。
- 保存记录列表只用于查询和追溯；文件选择与派发参数统一在“文件推送”页面完成。当前允许推送 `.GEN` 和 `.TXT` 文件，不提供内容预览、不执行文件内容，也不会自动生成派发任务。
- 页面及保存接口仅管理员可用。推送读取系统文件前会校验路径、大小和 SHA-256；接口错误页、空文件、不完整文件不标记为保存成功。
- 默认单文件上限50MiB、单次请求超时60秒、最多同时2个下载。程序重启将未完成记录标为失败。成功下载的实体文件默认保留7天，服务启动时及此后每天检查并自动清理；SQLite下载记录继续保留到派发记录保留期，需要时可重新保存。`FILE_DOWNLOAD_RETENTION_DAYS=0` 可关闭实体文件自动清理。
- 统一保留任务每天清理已终结的运行数据：报告文件、生成任务和车间任务快照默认365天、智能分析版本90天、已关闭告警365天、非活动派发记录365天、确认与管理员权限审计730天。各项可通过 `REPORT_RETENTION_DAYS`、`ANALYSIS_RETENTION_DAYS`、`ALARM_RETENTION_DAYS`、`DISPATCH_RETENTION_DAYS`、`AUDIT_RETENTION_DAYS` 调整，设为0只关闭对应类别。
- 文件服务认证只放后端 `.env` 的 `FILE_HTTP_AUTHORIZATION` / `FILE_HTTP_COOKIE`；若接口不需要则留空。不会复用能管授权码，不向前端输出凭据，不自动跟随下载重定向。

部署配置见 [`README-DOCKER.md`](README-DOCKER.md) 的“文件下载配置”。

登录后按角色分流：管理员进入完整管理主站，在“车间报表”查看任务、确认人和确认时间，并可对误确认执行有原因、可追溯的撤销或改派，不能代替工人新增本人确认；工人账号直接进入独立的“我的任务确认”页，不显示领导报表、数据库、报表设计等管理导航。新增确认和本人撤销 API 限制为工人角色，管理更正和审计查询 API 限制为管理员。

后端权限与前端角色保持一致，不能通过直接调用 API 绕过菜单限制：

- 公开接口仅包括账号初始化、管理员登录、工人数据库登录、登录状态和健康检查；
- 报告生成/历史/文件、领导报表、数据库管理、连接测试、查询选项和报表设计仅允许管理员访问；
- 车间日报及任务明细允许所有已登录员工查看，任务确认及本人撤销只允许工人账号，撤销他人确认、改派和审计查询只允许管理员；
- 演示数据模式不会绕过登录或管理员权限；
- SQL Server 模式下，SQL 工作台和报表设计在管理员登录之外继续要求独立高强度令牌。

生产环境建议通过 HTTPS 访问，并在 `.env` 开启安全 Cookie：

```env
AUTH_SESSION_HOURS=12
AUTH_COOKIE_SECURE=true
```

## 目录

```text
backend/      Python/FastAPI 后端
frontend/     Vue 3/Vite 前端
docs/         需求、设计、部署、数据库和外部接口资料
scripts/      安装、启动及后续维护脚本
packages/     本地部署包；ZIP文件不提交Git
vendor/       报告内嵌的 ECharts 资源，由前端构建脚本生成
data/         SQLite 元数据，运行后生成
reports/      HTML 报告，运行后生成
logs/         日志目录
```

## 首次安装

构建前端需要 Node.js 20.19 或更高版本。前端使用 Soybean Admin v2.3.0 的布局基础与 Naive UI，继续通过 npm 安装和构建。

在项目根目录执行：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r backend/requirements-dev.txt

cd frontend
npm install
npm run build
cd ..
```

`npm run build` 会同时将 `echarts.min.js` 同步到 `vendor/`，用于生成真正不依赖服务器的单文件报告。

## 生产式本地启动

先构建 Vue，再由 FastAPI 同时提供页面和 API：

```bash
./.venv/bin/python -m uvicorn app.main:app \
  --app-dir backend \
  --host 127.0.0.1 \
  --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

## Docker 一键部署

项目提供多阶段 `Dockerfile` 和 `compose.yaml`：Node 阶段构建 Vue，Python 阶段安装后端依赖，最终镜像包含 Microsoft ODBC Driver 18。Compose 会强制使用生产模式；镜像使用非 root 用户、只读根文件系统、健康检查和日志轮转。容器内只启动一个 Uvicorn 进程，避免当前 SQLite 和报告后台线程被多进程重复使用。

首次部署：

```bash
cp .env.example .env
# 编辑 .env，填写数据源、SQL Server 和可选 LLM 配置
docker compose up -d --build
```

查看状态和日志：

```bash
docker compose ps
docker compose logs -f app
```

Docker 默认仅监听宿主机 `127.0.0.1:8088`，适合在前方配置 Nginx/IIS HTTPS 反向代理。只在受控内网临时直接访问时，才将 `.env` 的 `DOCKER_BIND_ADDRESS` 改为 `0.0.0.0`。容器内部服务端口仍为 `8000`。

容器直接使用 SQL Server 只读连接，不挂载 Excel 输入目录。`.env`、原始 Excel 资料和运行数据不会被写入镜像。`data`、`reports` 和 `logs` 使用 Docker 命名卷持久化，执行 `docker compose down` 不会删除；不要使用 `docker compose down -v`，除非已备份且确实需要清空数据。

更新代码后重建：

```bash
docker compose up -d --build
```

停止服务：

```bash
docker compose down
```

## HTML / PDF 导出

领导、车间和设备报表右上角的“导出报表”下拉框提供“导出 HTML”和“下载 PDF”。
HTML 保留原有生成与历史流程；PDF 生成完成后直接下载，使用与当前查询相同的起止日期和图表类型。
生成结果及历史列表也提供“下载 PDF”，无需重新查询业务数据。PDF 按 A4 横向排版，
包含完整表格、图表与智能分析；首次按需转换已归档 HTML，此后复用同一 PDF。

Docker 镜像包含 Chromium 和中文字体，升级后需执行 `docker compose up -d --build`。
原生部署先更新 Python 依赖，再使用运行服务的同一账号安装浏览器：

```bash
python -m pip install -r backend/requirements.txt
python -m playwright install chromium
```

Windows 的 `scripts/windows/install_windows.bat` 已包含浏览器安装步骤。
Linux 原生部署使用 `python -m playwright install --with-deps chromium`，并安装中文字体
（Debian/Ubuntu：`fonts-noto-cjk`）。内网离线部署需事先在联网环境准备包含浏览器的镜像；
原生离线部署需一并准备与 Playwright 版本匹配的浏览器，可用 `PLAYWRIGHT_BROWSERS_PATH`
指定位置。HTML 导出不依赖浏览器；PDF 渲染失败会提示错误，可在历史列表重试。

下载接口：`GET /api/v1/reports/{report_id}/download?format=pdf`，仅管理员可访问；
省略 `format` 时仍下载 HTML。PDF 转换只读取已归档报告，并阻止渲染页面发起外部网络请求。

## 配置驱动报表设计

顶部导航的“报表设计”提供三个相互独立的配置层：

1. **查询任务**：维护只读 T-SQL、返回字段、字段类型和单位；支持语法校验与数据预览。
2. **报表定义**：选择查询任务和模板，配置周期、明细列、汇总指标、分类轴和图表系列。
3. **模板设计器**：配置标题、页眉标识、主题颜色、页面背景、单双栏布局、表格密度和图表高度，并实时预览。

查询 SQL 支持以下命名参数，执行时会转换为 SQL Server 参数化查询：

- `:period_start`：所选日/月的开始日期
- `:period_end`：所选日/月的结束边界（不包含）
- `:report_date`：用户选择的报告日期
- `:project_code`：工程号
- `:ship_no`：船号

保存并启用报表定义后，它会自动出现在“生产报告”的报告类型列表中。系统内置的领导、车间和设备报表为只读定义，继续使用原有专用统计管线；内置模板可复制后修改。

SQL Server 模式下，报表设计管理接口使用与 SQL 工作台相同的管理员令牌：

```env
SQL_WORKBENCH_ADMIN_TOKEN=请设置高强度令牌
```

报表定义、查询任务和模板均保存在 `data/metadata.db`，每次修改自动递增版本号；已生成的 HTML 仍为单文件离线报告。

API 文档：

```text
http://127.0.0.1:8000/api/docs
```

## 前后端开发模式

终端一：

```bash
./.venv/bin/python -m uvicorn app.main:app \
  --app-dir backend \
  --reload \
  --port 8000
```

终端二：

```bash
cd frontend
npm run dev
```

打开 `http://127.0.0.1:5173`。Vite 会将 `/api` 和 `/reports` 转发到 FastAPI。

## 测试

```bash
./.venv/bin/python -m pytest backend/tests
```

前端构建检查：

```bash
cd frontend
npm run build
```

## 切换 SQL Server

1. 在 Windows 服务器安装 Microsoft ODBC Driver for SQL Server；
2. 安装额外依赖：

   ```bash
   .venv\Scripts\python.exe -m pip install -r backend\requirements-sqlserver.txt
   ```

3. 根据实际数据库结构替换以下 SQL：

   - `backend/app/database/queries/summary_base.sql`
   - `backend/app/database/queries/workshop.sql`
   - `backend/app/database/queries/equipment.sql`

4. 确保 SQL 返回的英文别名与概要设计数据契约一致；
5. 在 `.env` 填写 SQL Server 连接配置；
6. 将 `APP_DATA_SOURCE_MODE` 改为 `sqlserver`。

生产库账号必须为只读账号，不应使用 `sa`。

可以先在网页的“数据库管理”中填写连接参数并执行一次性连接测试。测试成功后，再由管理员将正式参数写入服务器 `.env`；当前版本有意不从匿名内网页面持久化数据库密码。

### 启用SQL工作台执行功能

SQL Server 模式的执行功能默认关闭；完成管理员访问控制和 HTTPS 部署后，在 `.env` 配置：

```text
SQL_WORKBENCH_ENABLED=true
SQL_WORKBENCH_ADMIN_TOKEN=请使用高强度随机令牌
SQL_WORKBENCH_MAX_ROWS=200
```

同时必须确保SQL Server业务账号只有必要对象的`SELECT`权限。修改配置后需重启后端服务。

## 启用 LLM

在 `.env` 配置：

```text
LLM_ENABLED=true
LLM_API_KEY=...
LLM_BASE_URL=https://模型服务地址/v1
LLM_MODEL=模型名称
```

`LLM_ENABLED=false` 时，系统基于当前报表数据生成可重现的模拟分析。改为 `true` 并配置 OpenAI 兼容的服务后，页面与导出报告会自动切换为真实模型分析。模型失败时自动降级为模拟分析，不会阻断报表数据展示。

每个报表的智能分析只保留六项核心数据依据，统一编号 F001–F006：

- 领导报表：当月任务数、切割长度、切割时长、任务数环比、任务数同比、主要工程。
- 车间报表：任务总数、总切割长度、总切割时长、工人确认率、未确认任务、工时异常任务。
- 设备报表：任务总数、总切割时长、已匹配任务气耗、电耗、工时异常任务、能耗数据待核对任务。

能耗数据待核对任务按任务计数，同一任务有多类问题只计一次，来源中保留重叠、读数缺口和时间异常说明。
没有可比数据时显示“无法比较”，不将缺失数据补成零。分析正文只引用这六项，页面及新生成的 HTML/PDF
使用相同依据；分析缓存版本已更新，历史归档文件保留生成时内容。

## 启用智能告警

智能告警从生产库 `dbo.alarm_info` 只读采集，首期只监控正式名称“20kw切割机”对应的
`MACHINE_NO=Cutter01`，不包含 Cutter02。后台每五分钟检查一次，但只有发现新的告警问题时才调用大模型并立即生成实体
PDF；同一问题持续未解除时只更新事件状态，不重复生成。问题解除后十分钟内复发视为抖动，
仍归入原事件；超过该窗口再次出现才生成新报告。系统只采集配置起始时间及之后的
`START_TIME`，首次启用该口径时自动从起点补录，后续按 `ALARM_INFO_ID` 游标增量读取。

在 `.env` 配置：

```text
ALARM_MONITOR_ENABLED=true
ALARM_MONITOR_INTERVAL_SECONDS=300
ALARM_MONITOR_MACHINE_NOS=Cutter01
ALARM_MONITOR_START_AT=2026-09-29 00:00:00
ALARM_REOPEN_DEBOUNCE_MINUTES=10
ALARM_MONITOR_BATCH_SIZE=500
```

告警指纹优先由设备名、告警类型、编码和类别组成；没有告警编码时才使用规范化描述，且不包含开始、结束或持续时间。
事件状态、同步游标及报告记录保存在 `data/metadata.db`，因此服务重启不会造成重复报告。
起始时间首次变化时，服务会自动重置游标并按新口径补录；起始日前的本地旧事件仍保留审计，
但不会进入页面列表、统计或新报告。需要人工重新同步当前起始日时，先停服务，在服务器项目目录运行
`python scripts/prepare_alarm_monitor.py` 检查配置，再运行
`python scripts/prepare_alarm_monitor.py --apply`，确认成功后启动服务。
管理员可从顶部“告警”页面查看事件、预览 HTML，并下载实体 PDF。大模型不可用时不生成
规则分析报告；生成失败会保留告警事件与失败状态，后续扫描可重试。

## 启用报表自动化

管理员可在“自动化”页面配置每日、每周或每月的报表任务。系统会自动生成报表、发送 HTML 附件，失败时按指数退避策略重试，并持久保存每次发送结果。

在 `.env` 配置 SMTP：

```text
AUTOMATION_ENABLED=true
AUTOMATION_SCAN_SECONDS=15
EMAIL_RETRY_BASE_SECONDS=60
SMTP_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=report@example.com
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=report@example.com
SMTP_FROM_NAME=切割生产智能产报系统
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

587 端口通常使用 `SMTP_USE_TLS=true`；465 端口通常使用 `SMTP_USE_SSL=true` 并将 TLS 设为 `false`。修改配置后需重启服务。SMTP 未配置时定时任务仍可保存，但发送会按规则重试并在发送记录中显示失败原因。

## 上线 SQL Server 前待确认

- SQL Server 网络、ODBC 驱动和只读账号；
- `TASK_ID` 是否始终为任务唯一键；
- 生产日期/班次口径；
- 能管仪表对应的 MACHINE_NO、DH01～DH03 的独立计量边界及转换因子；
- 正式异常阈值；
- 内网身份认证与报告保留策略。
