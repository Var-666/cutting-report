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
- Excel 模式会把 `TASK.xlsx` 加载到内存只读副本，原文件不会被 SQL 修改；
- SQL Server 模式使用服务器配置的只读账号，并要求独立管理员令牌；
- SQL 正文不写入普通业务日志。

领导、车间和设备报表已按 SQL Server 的 `dbo.task` 表完成查询映射；当前 `.env` 默认使用 Excel 模式，不连接生产数据库。

Excel 模式配置：

```env
APP_DATA_SOURCE_MODE=excel
TASK_EXCEL_PATH=TASK.xlsx
```

文件更新后无需导入数据库，下一次查询会按文件修改时间自动刷新缓存。工作表名称必须为 `TASK`，并保留原始字段名。

领导报表统计口径：仅统计 `STATUS = 2` 且 `WORK_TIME >= 0` 的任务，负工时异常任务整条剔除；优先按 `OP_DATE` 归属日期，无法转换时回退 `START_TIME`；切割和坡口长度由毫米换算为米；`WORK_TIME` 按秒汇总；历史对比覆盖所选月份及其前十一个月；图表时间轴从左到右递增；不按 `PORTION_ID` 去重。

首页概览与领导报表共享按月份缓存，设备日报与智能分析共享按日期缓存，默认有效期均为 300 秒；相同月份或日期的并发请求只执行一次数据库查询。三张报表在前端切换时保留页面状态，不会因为切换重复加载或重新请求智能分析。点击“刷新报表”会强制读取最新数据。可通过 `LEADERSHIP_REPORT_CACHE_SECONDS` 和 `REPORT_DATA_CACHE_SECONDS` 分别调整缓存时间，设为 `0` 可关闭对应缓存。

三类导出报告：

管理员登录后默认进入独立的生产管理首页，首页展示本月核心指标、三类报表入口、最近生成记录和管理快捷入口；“生成与历史”仅作为报表栏目下的功能页保留。

- 领导报表按月生成，包含当月汇总、每日长度/工时趋势、最近十二个月趋势、按日明细和历月对比；
- 车间报表按日生成，包含生产指标、任务切割长度/坡口长度/工时组合图、任务明细和工人确认统计；
- 设备报表按日生成，包含设备生产指标、按时间排序的能耗图和设备明细；真实数据模式（excel/sqlserver）通过能管接口按任务实际起止时间计算气耗、电耗。未配置、未绑定、缺数或接口失败时显示 `--`，不回退到模拟值。只有显式 `demo` 模式生成标注清楚的演示能耗，不能用于结算或考核。
- 三类报表均包含报表专用智能分析；未配置模型时明确显示“模拟大模型分析”，并按报表类型、日期和数据内容缓存。

设备报表拥有独立展示页，进入后自动定位并查询最近一个有数据的生产日期；任务明细按“序号、工程号、船号、分段名、开始时间、切割时长、电耗、气耗”展示，并按任务开始时间从近到远排列。

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
- “查看月份”读取已保存记录；“同步本月”依次查询缺失、不完整日期及今日；
  “重新查询本月”重新查询全部已到日期。同步有进度、可以停止；已完成日期不会丢失。
  这是按需查询与保存，不是后台自动每日采集，历史记录不会在月底自动删除。
- 小时区间严格使用整点至下一整点；全天为本地时区 00:00 至次日 00:00。
  不能将当天第一条、最后一条读数直接当作日界。今日只统计已结束小时，未结束小时和未来日期不填零。
- 合计只加有效小时/日期；缺失保持 `--`，不完整合计明确标记“部分合计”，同时展示覆盖数。
  所选电表任意一块缺数时，该小时电耗整体缺失；非精确边界按配置规则进行插值估算，不在数值前添加符号。
- 日/月报独立使用 `ENERGY_REPORT_MAX_GAP_SECONDS=1800`，允许目前看到的15分钟采样；
  不修改任务报表原有300秒阈值。后续一分钟采样无需改核心算法，仍须确认实际读数口径。
- 电耗单位统一使用 `kWh`（1 kWh = 1 度电），气量单位使用 `Nm³`。
- 保存记录按接口地址、账号标识、参数组、单位、换算因子、时区和计算规则隔离。
  修改这些配置后不会混用旧口径记录。网络/认证失败，或有效时段减少时，不覆盖此前已保存的较完整记录，并提示保留原因。

启动真实查询最少需在内网部署 `.env` 配置 `ENERGY_API_ENABLED=true`、
`ENERGY_API_BASE_URL`、`ENERGY_API_USERNAME`、`ENERGY_API_PASSWORD`；
电表组默认值见 `.env.example` 的 `ENERGY_REPORT_ELECTRICITY_PARAMETER_IDS`，
气表仍使用 `ENERGY_GAS_PARAMETER_ID`。日/月报无需配置 `ENERGY_EQUIPMENT_IDS`。

接口：管理员会话下 `GET /api/v1/energy-report/tasks?report_date=YYYY-MM-DD&refresh=true`
查询任务能耗；`GET /api/v1/energy-report/day?report_date=YYYY-MM-DD&refresh=true` 查询并保存
全天内部统计；`GET /api/v1/energy-report/month?report_month=YYYY-MM` 读取整月记录。

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

采集接口返回 `MfId / Mt / Mv`。客户端兼容数组、JSON 字符串和 `Content` 包装，登录自动获取 CompanyId/AuthId，鉴权过期最多重登一次；网络失败短暂退避，页面、导出与分析共享短期读数缓存，手动刷新绕过缓存。查询按最多 24 小时拆分，最长跨八天（含边界缓冲），不长期保存原始采集数据。

计算及质量口径：

- 必须精确匹配 `MACHINE_NO`；可配置同一设备的历史别名，但不能关联多台不同设备。
- 气耗 = 累计气量结束读数 − 开始读数；电耗 = 已确认的电表各自正向有功电能差值之和。任一所选电表缺数时，该任务电耗整体缺失，不伪造少表的合计值。
- 使用任务 `START_TIME` 和 **实际 `END_TIME`**，支持跨午夜；不以 `START_TIME + WORK_TIME` 冒充结束时刻。无效时间或同一计量设备在查询任务集中存在重叠时，不分摊、不重复计量。跨报表日的重叠任务仍需现场核对。
- 非采样时刻的起止边界用相邻读数线性插值估算；数值列不添加额外符号。所有区间内相邻采样间隔都必须在配置上限内。设 `ENERGY_ALLOW_INTERPOLATION=false` 时仅接受精确边界读数，不外推。采样缺口、回零/倒退、负值、非有限数、同刻冲突均保留缺失及原因。
- 文档注明上传的 `Mv` 已乘读数因子，默认转换因子为 1，不重复乘 CT/PT。若实际返回单位不是目标单位，必须经确认后设置转换因子；不从“2万瓦”额定功率反推用电量。
- KPI 是**已匹配任务时间区间耗量**（含该区间内待机），不是全天总用量或纯切割净耗；分别展示气、电有效记录覆盖数。空值不作为零，真实零耗量可正常显示。插值值、缺失和部分合计不可用于结算或直接判定能耗异常。

内网只读联调（Windows CMD，已安装后端依赖）：

```bat
.venv\Scripts\python.exe scripts\check_energy.py --date 2026-08-31
```

未配置账号时会询问账号，密码采用隐藏输入；成功后仅显示四个参数的采样条数和首末累计读数，不显示授权码，也不把首末值差误称全天用量。可追加 `--output data\energy_probe.json` 导出原始读数用于校验（文件必须不存在）。生产数据日期必须落在仪表实际有采样的时间内，不能将当天读数补给旧报表。接口使用明文 HTTP GET 传密码，建议由内网 HTTPS 网关保护，勿使用 `curl -v` 分享包含密码的请求日志。

三类内置报表均可在生成前选择柱线组合、柱状图、折线图或面积图。领导报表的页面图表会立即切换，所选类型同时写入导出的 HTML；车间和设备报表在导出时应用所选类型。自定义报表定义也支持这四种图形。

导出的 HTML 已内置 A4 横向打印样式。使用浏览器“打印 → 另存为 PDF”时，图表会在打印前重新适配页面宽度，明细表按页拆分并在每页重复表头，避免横向溢出、列宽漂移和跨页断行。历史 HTML 文件不会自动套用新模板，需要重新生成报告。

车间任务的“工程号”和“船号”均原样显示 `PROJECT_NAME`。每个生产日期只能生成一份车间报表：系统先把当次 SQL/`task` 模拟查询结果写入 `workshop_reports` 和 `workshop_report_items`，分页和筛选只影响展示，不会重新查询或改变报表明细。所有工人均可查看整份报表。

人工确认以 `TASK_ID` 为唯一任务键，表示“这个任务是我完成的”，一个任务只能由一个登录账号确认。确认记录保存在系统 SQLite 的 `workshop_confirmations` 表中，不修改生产库 `dbo.task`。车间 HTML/PDF 导出使用同一份日报快照，生成时会固化当时的确认人；之后新增确认需重新执行导出。

## 账号与车间确认

系统首次启动且管理员账号表为空时，登录页会要求创建首个本地管理员账号。启用工人数据库认证后，工人使用公司工号和 HR 数据库中的密码登录，管理员仍使用本系统账号登录。任意已登录员工首次进入当日页面时，系统会幂等地生成或读取当日唯一报表；页面每 5 秒静默同步一次其他员工的确认结果。点击“确认是我完成”后，系统立即记录当前账号姓名和确认时间，不允许第二个人覆盖。

工人认证读取 SQL Server 的 `dbo.HRbase_userInfo`，字段映射为：`公司工号`是登录账号、`密码`在 SQL Server 内直接比较、`_Identify`是稳定人员标识、`姓名`是显示姓名，同时读取`集团工号`、`可用`和`_Locked`。明文密码不会被查询返回、写入本地 SQLite、Session 或普通日志。工人首次登录成功时，系统只在 SQLite 创建一条不含密码的影子身份，用于会话和任务确认外键。

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
WORKER_DATABASE_UNLOCKED_VALUE=0
```

后两个值必须与正式表中的实际数据一致。工号不存在、密码错误、账号不可用、账号锁定或公司工号重复时，对外统一提示“公司工号或密码错误”。数据库连接失败时返回“工人登录数据库暂时不可用”。

切割文件库使用 `FILE_SQLSERVER_*` 独立配置，关联为 `PDM_DesignDocumentInfo.ID = PDM_DocumentFileInfo.DocumentID`，并严格匹配版本。真实文件的 `FilePath` 作为 FO HTTP 下载接口的相对路径，需在现场核对一条文档及版本记录。PDM 只读连接配置完成后可启用 `FILE_DATABASE_ENABLED=true`；下载与魔盒接入相互独立。三套库的只读账号和连接参数可在“设置 → 数据库配置”中维护；密码只能更新、不会回显。配置保存到 `data/settings.env`，重启服务后生效。

文件页面支持可增减的组合条件检索、分页返回文件、下载到系统本地、选择已下载文件，以及上传魔盒并指派机床。
“文件派发”作为父级目录，分为“文件下载”和“文件推送”：

- 文件下载：`/admin/cutting-dispatch/downloads`，负责条件检索、下载下拉选择、本地文件记录及另存到电脑，不显示派发参数和历史。
- 文件推送：`/admin/cutting-dispatch/push`，不提供条件检索；只从已下载且通过校验的本地文件中选择，填写机床及加工参数、保存草稿、明确确认推送和查看分阶段记录。
- 确认推送会复用经过大小及 SHA-256 校验的准确版本，本地不存在时先从 FO 下载，再调用魔盒上传和指派接口。`taskName` 与 multipart 文件名均使用实际上传文件的文件名（含扩展名）。
- 上传、指派独立落库；指派失败重试时不重复上传。上传超时或服务在上传阶段重启时会标记“上传结果待核对”，禁止自动重复上传。
- 原 `/admin/cutting-dispatch` 地址及同名路由自动进入文件下载，子页面也支持直接访问、刷新；仍需管理员权限。

`FILE_DATABASE_ENABLED=false` 时清单为明确标识的模拟PDM数据；默认 `FILE_SOURCE_MODE=disabled`。
真实 HTTP 下载必须同时启用真实 PDM，不能用模拟记录请求真实文件服务；模拟 PDM/文件也不允许推送真实魔盒。保存草稿不会下载、上传或回写 PDM。

#### 通用动态条件查询

网页增删条件 → 提交结构化条件 → 后端验证字段和比较方式 → 动态拼接参数化查询 → 数据库筛选、分页返回。
不是先将所有文件加载到网页后再过滤。进入页面不会自动查询 PDM 文件，至少添加一条条件后点击“查询文件”。

- 每条条件由“字段、比较方式、条件值”组成，可选择“全部满足（AND）”或“任一满足（OR）”；本版为单层条件组，不支持嵌套括号组。
- PDM 条件按“工程信息、文件分类/分组、设计文档、PDM下载/派发、物理文件”分组。支持船号、工程名、ProjectInfoID、FileID、分组名称、DocumentNo、中英文名、PBSInfo、VersionID/版本、操作人及日期、Custom1—Custom6业务字段，以及物理文件名、类型、VersionNo、VersionCode、FileDate和FilePath；本地下载记录支持名称、版本、日期、操作人、大小、状态、来源等字段。
- 字段元数据及允许的运算由后端统一返回，前端复用 `ConditionBuilder.vue`。文本支持包含、等于、前后缀、多值、空值等；日期和数字支持比较、区间；状态使用后端选项。
- 默认每页10条，接口最多100条；条件可动态增减，单次最多50条，每个多值条件最多50个值、整次最多1000个比较值，防止请求膨胀和数据库参数超限。
- 查询结果与总数使用同一条件。日期区间包含起止日期，大小按字节；`不等于`、`不包含`、`不属于`不自动包含 NULL，需要查空值时选择“为空”。文本比较大小写以生产库排序规则为准。
- 修改条件后旧清单不可下载、选择或保存派发参数，重新查询成功后恢复。下载下拉框仅显示当前查询页；本地记录可不加条件，仍分页显示。
- 条件只负责筛选，不会触发下载或推送。实际下载仍需明确选中文档；后续推送必须绑定具体文件、版本和目标，再确认执行，不能把匹配条件直接当作执行授权。

管理员接口：`GET /api/v1/conditions/{dataset}/schema`，其中 `dataset` 为 `cutting_files` 或 `cutting_downloads`。
查询分别为 `POST /api/v1/cutting/files/search`、`POST /api/v1/cutting/downloads/search`，请求格式相同，例如：

```json
{
  "filters": {
    "logic": "and",
    "rules": [
      {"field": "ship_no", "operator": "eq", "value": "CMYZ0177"},
      {"field": "document_no", "operator": "contains", "value": "甲板"},
      {"field": "operate_date", "operator": "between", "value": ["2026-08-01", "2026-08-31"]}
    ]
  },
  "page": 1,
  "page_size": 10
}
```

规则模型位于 `backend/app/models/conditions.py`，字段注册、验证和 SQL 编译位于 `backend/app/services/condition_service.py`。
SQL 列名和表达式由后端白名单定义，用户值只作为绑定参数，`%`、`_` 等在包含查询中作为普通字符处理；不接受前端 SQL、表名或任意列名。
未来接入其他查询或推送候选列表时，增加数据集字段配置和对应数据访问适配器，复用相同规则、校验与页面组件。
本次的魔盒推送仅由管理员对单个文件明确确认触发，尚未实现自动推送或条件收藏，也不回写 PDM 状态。
原项目/目录/固定查询 GET 接口保留兼容，但新页面不再使用其固定筛选表单。

#### 文件本地下载与选择

- “选择需要下载的文件”下拉框与当前查询页的文件清单联动，展示文档名和版本；更换检索条件或分页可选择其他文件。
- 点击下载后，后端按文档ID重新查询 PDM，将版本和路径与页面选择核对，再读取接口。找不到对应版本、存在多个物理文件或列表已过期时明确报错，不回退其他版本。
- 接口为 `GET /FOWebApi/api/FO/FilePreView`，两个 Query 参数为 `filepath` 和 `contentType=application/octet-stream`；不需 SMB/FTP。
- 使用数据库 `DocumentNo` 作为下载文件名，不拼接版本号；名称缺少扩展名时补用 `FilePath` 的原扩展名，并清理 Windows 不支持的字符。
- 每次下载保存到独立目录 `data/cutting-downloads/<下载ID>/<文件名>`，记录进入 SQLite `cutting_downloads`，包含名称、版本、大小、SHA-256、时间和操作人。同名或重试不会覆盖已完成文件。
- Docker 中“本地”指部署服务器的数据卷 `/app/data/cutting-downloads`，并非浏览器电脑的磁盘目录。“另存到电脑”才会交给浏览器保存。
- 已下载列表支持单选和取消选择，显示保存位置；不提供内容预览、不执行 GEN，不自动生成派发任务或改变推送页参数。
- 页面、保存及下载接口仅管理员可用。读取本地附件前校验路径、大小和 SHA-256；接口错误页、空文件、不完整文件不标记为下载成功。
- 默认单文件上限50MiB、单次请求超时60秒、最多同时2个下载。程序重启将未完成记录标为失败，已完成文件保留。文件不自动删除，运维需关注磁盘空间并随 data 卷备份。
- 文件服务认证只放后端 `.env` 的 `FILE_HTTP_AUTHORIZATION` / `FILE_HTTP_COOKIE`；若接口不需要则留空。不会复用能管授权码，不向前端输出凭据，不自动跟随下载重定向。

部署配置见 [`README-DOCKER.md`](README-DOCKER.md) 的“文件下载配置”。

登录后按角色分流：管理员进入完整管理主站，其“车间报表”页只读展示任务、确认人和确认时间，不能代替工人确认；工人账号直接进入独立的“我的任务确认”页，不显示领导报表、数据库、报表设计等管理导航。确认 API 同样限制为工人角色。

后端权限与前端角色保持一致，不能通过直接调用 API 绕过菜单限制：

- 公开接口仅包括账号初始化、管理员登录、工人数据库登录、登录状态和健康检查；
- 报告生成/历史/文件、领导报表、数据库管理、连接测试、查询选项和报表设计仅允许管理员访问；
- 车间日报及任务明细允许所有已登录员工查看，任务确认只允许工人账号；
- 演示数据模式和 Excel 模式不会绕过登录或管理员权限；
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

默认仅监听宿主机 `127.0.0.1:8000`，适合在前方配置 Nginx/IIS HTTPS 反向代理。只在受控内网临时直接访问时，才将 `.env` 的 `DOCKER_BIND_ADDRESS` 改为 `0.0.0.0`。

Excel 联调模式需要将数据文件放到：

```text
docker/input/TASK.xlsx
```

SQL Server 模式不需要该文件。`.env`、Excel 数据和运行数据不会被写入镜像。`data`、`reports` 和 `logs` 使用 Docker 命名卷持久化，执行 `docker compose down` 不会删除；不要使用 `docker compose down -v`，除非已备份且确实需要清空数据。

更新代码后重建：

```bash
docker compose up -d --build
```

停止服务：

```bash
docker compose down
```

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

Excel 模式的只读 SQL 自动可用。SQL Server 模式的执行功能默认关闭；完成管理员访问控制和 HTTPS 部署后，在 `.env` 配置：

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

当前分析上下文已包含可用的同比/环比、统计异常点、工程分布与编号数据依据。分析文字中的 `[F001]` 类引用可与页面及导出报告中的“可追溯数据依据”对照。

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
