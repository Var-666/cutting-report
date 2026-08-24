# 切割生产智能产报系统

基于 Vue 3、FastAPI、SQL Server、ECharts 和 OpenAI-Compatible LLM API 的内网生产报告系统。

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

三类导出报告：

管理员登录后默认进入独立的生产管理首页，首页展示本月核心指标、三类报表入口、最近生成记录和管理快捷入口；“生成与历史”仅作为报表栏目下的功能页保留。

- 领导报表按月生成，包含当月汇总、每日长度/工时趋势、最近十二个月趋势、按日明细和历月对比；
- 车间报表按日生成，包含生产指标、任务切割长度/坡口长度/工时组合图、任务明细和工人确认统计；
- 设备报表按日生成，包含设备生产指标、按时间排序的能耗图和设备明细；正式能耗字段接入前，气耗和电耗使用按任务稳定生成的模拟值，并在页面及导出文件中明确标记为“模拟数据（仅用于联调）”，不得用于结算或考核。
- 三类报表均包含报表专用智能分析；未配置模型时明确显示“模拟大模型分析”，并按报表类型、日期和数据内容缓存。

设备报表拥有独立展示页，进入后自动定位并查询最近一个有数据的生产日期；页面按“序号、工程号、船号、分段名、开始时间、切割时长、气耗、电耗、备注”展示，每页 10 条，并按开始时间从早到晚排列。

设备页面和导出文件的图表只使用上述明细表可见字段：以开始时间为横轴，展示切割时长、气耗和电耗；不使用隐藏的设备编号、切割长度或任务数生成额外图表。

三类内置报表均可在生成前选择柱线组合、柱状图、折线图或面积图。领导报表的页面图表会立即切换，所选类型同时写入导出的 HTML；车间和设备报表在导出时应用所选类型。自定义报表定义也支持这四种图形。

导出的 HTML 已内置 A4 横向打印样式。使用浏览器“打印 → 另存为 PDF”时，图表会在打印前重新适配页面宽度，明细表按页拆分并在每页重复表头，避免横向溢出、列宽漂移和跨页断行。历史 HTML 文件不会自动套用新模板，需要重新生成报告。

车间任务的“工程号”和“船号”均原样显示 `PROJECT_NAME`。每个生产日期只能生成一份车间报表：系统先把当次 SQL/`task` 模拟查询结果写入 `workshop_reports` 和 `workshop_report_items`，分页和筛选只影响展示，不会重新查询或改变报表明细。所有工人均可查看整份报表。

人工确认以 `TASK_ID` 为唯一任务键，表示“这个任务是我完成的”，一个任务只能由一个登录账号确认。确认记录保存在系统 SQLite 的 `workshop_confirmations` 表中，不修改生产库 `dbo.task`。车间 HTML/PDF 导出使用同一份日报快照，生成时会固化当时的确认人；之后新增确认需重新执行导出。

## 账号与车间确认

系统首次启动且账号表为空时，登录页会要求创建首个管理员账号。管理员可在“车间报表”页面创建工人账号。任意已登录员工首次进入当日页面时，系统会幂等地生成或读取当日唯一报表；页面每 5 秒静默同步一次其他员工的确认结果。点击“确认是我完成”后，系统立即记录当前账号姓名和确认时间，不允许第二个人覆盖。

登录后按角色分流：管理员进入完整管理主站，其“车间报表”页只读展示任务、确认人和确认时间，不能代替工人确认；工人账号直接进入独立的“我的任务确认”页，不显示领导报表、数据库、报表设计等管理导航。确认 API 同样限制为工人角色。

后端权限与前端角色保持一致，不能通过直接调用 API 绕过菜单限制：

- 公开接口仅包括账号初始化、登录、登录状态和健康检查；
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

## 上线 SQL Server 前待确认

- SQL Server 网络、ODBC 驱动和只读账号；
- `TASK_ID` 是否始终为任务唯一键；
- 生产日期/班次口径；
- 气耗和电耗的实际关联表与单位；
- 正式异常阈值；
- 内网身份认证与报告保留策略。
