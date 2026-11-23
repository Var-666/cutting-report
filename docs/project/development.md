# 开发指南

本页说明源码开发环境、常用命令和依赖维护。部署操作从 [文档目录](../README.md) 选择对应流程，业务边界与测试要求见 [开发规范](../../AGENTS.md)。

## 环境准备

- Python：默认版本见 [`backend/.python-version`](../../backend/.python-version)，当前为 3.12.14。
- uv：0.13.6，与 `backend/pyproject.toml` 的 `required-version` 一致；安装方式见 [uv 官方说明](https://docs.astral.sh/uv/getting-started/installation/)。
- Node.js：至少 22.18；pnpm 版本由 `frontend/package.json` 的 `packageManager` 固定，当前为 10.34.9。
- PostgreSQL：准备可从本机访问的 PostgreSQL 18，按 [状态库维护说明](../deployment/PostgreSQL状态库迁移.md) 创建账号并安装 Schema。

演示模式使用模拟生产数据，仍需 PostgreSQL 保存账号、会话、快照和任务记录。

## 安装与配置

以下命令在项目根目录执行，适用于 macOS/Linux。已有 `.env` 时跳过复制；已有虚拟环境时先确认其归属，uv 同步会移除该环境中未声明的额外依赖。

```bash
cp .env.example .env
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project backend --locked --no-build
npm install --global pnpm@10.34.9
cd frontend
pnpm install --frozen-lockfile
cd ..
```

编辑 `.env`，将 `POSTGRES_HOST/PORT/DATABASE/USER/PASSWORD` 配置为本机可访问的状态库。模板中的 `POSTGRES_HOST=postgres` 是 Compose 服务名，直接在宿主机运行 Python 时应改为实际地址。应用使用 `cutting_app`，结构维护使用 `cutting_migrator`；普通启动不会安装 Schema。

管理员保存的 `data/settings.env` 优先于进程环境与 `.env`。已有运行数据时先核对 [配置来源与生效规则](../deployment/配置参考.md#来源优先级与生效诊断)，避免改到被覆盖的配置。

## 本地启动

分别在独立终端启动下列进程。后端命令均从项目根目录执行，各进程使用同一份配置与状态库。

```bash
# Web API
./.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

```bash
# 周期调度
PYTHONPATH=backend ./.venv/bin/python -m app.scheduler
```

```bash
# 后台任务；每个 Consumer 使用独立实例 ID
CONSUMER_INSTANCE_ID=dev-1 PYTHONPATH=backend ./.venv/bin/python -m app.worker
```

```bash
# 自动化计划与邮件
PYTHONPATH=backend ./.venv/bin/python -m app.automation_worker
```

```bash
# 前端
cd frontend
pnpm run dev
```

访问 [http://127.0.0.1:5173](http://127.0.0.1:5173)，Vite 将 `/api` 和 `/reports` 请求代理到本机 8000 端口。

连接真实 SQL Server 时需额外安装 SQL Server 可选依赖及 Microsoft ODBC Driver 18，见 [SQL Server 配置](../deployment/配置参考.md#切换-sql-server)。macOS 驱动安装参阅 [Microsoft 官方说明](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/install-microsoft-odbc-driver-sql-server-macos)，可通过 `./.venv/bin/python -c "import pyodbc; print(pyodbc.drivers())"` 检查加载结果。后续 uv 同步需保留 `--extra sqlserver`。

生成 PDF 需要 Playwright Chromium 和中文字体，安装步骤见 [PDF 运行环境](../deployment/配置参考.md#pdf-运行环境)。

## 前端开发

修改页面前查阅 `frontend/src/theme/`、`assets/style.css`、`uno.config.ts` 和现有组件。沿用 Soybean Admin、Naive UI 与 UnoCSS。颜色、背景、边框、文字和阴影用语义变量，Naive UI 变量由 `theme/reportVariables.ts` 桥接。验收覆盖亮色、暗色及主题色切换。页面专属样式放在 scoped CSS，全局样式只维护基础规则和跨页面规范。控件优先用 props、`theme-overrides` 或局部变量，确需 `!important` 时限定范围并说明覆盖原因。

### 布局与组件

- 用布局、层级和操作位置表达含义，保留必要的错误状态和数据依据。必要依据可在详情或悬停内容中查看，涉及操作的内容支持键盘和触屏。通用反馈及表单行为见[前端交互规范](frontend-interactions.md)。
- 沿用 4/8/12/16/24px 间距。辅助文字与控件默认 12px，正文 13px，区块标题 16px。报表筛选控件默认高 40px，控件圆角跟随 Naive 主题，卡片圆角 8px，报表面板圆角 10px。图表、紧凑表格及特殊业务布局可以局部调整。
- 表格、查询、日期、指标和趋势分别优先复用 `AppReportTable`、`StandardReportQuery`、`ReportPeriodPicker`、`HomeKpiCard` 和 `ReportTrendPanel`。

- 普通页面复用 `page-heading`、`page-heading__title` 和 `page-heading__actions`；报表头图标题复用 `report-hero-copy`。两者均沿用桌面 28px、窄屏 22px 的字号变量，允许标题换行，保留门户插画和报表头图。切割报表的紧凑 KPI 复用 `report-kpi-card` 的卡片与图标样式，业务指标布局和数据由页面维护。
- `StandardReportQuery` 按查询容器宽度排列 40px 控件；`ReportTrendPanel` 在窄屏保留时间范围和更新提示。`HomeKpiCard` 区分真实零值、加载骨架和无数据，失败和快照状态由页面反馈说明。`AppReportTable` 允许分页换行，表头、数据和汇总仍保持单行。

- 首页保持现有布局。门户按产线独立读取月累计，统计截止与统计生成时间分开显示，不跨工艺合计；切割概览按模块保留成功结果并重试失败项；焊接当日 KPI 与区间统计各自说明，报表快捷入口沿用已应用的区间和设备。

焊接首页与报表通过 `WeldingKpiCards` 组合 `HomeKpiCard`。首页数值 30px，报表使用 24px 指标变量，同一指标颜色保持一致。复杂筛选与标题组成同一查询区域。筛选展开、布局和主题切换不得重新提交已应用条件或触发额外请求。

### 表格

- 所有页面表格，包括管理页、弹窗与图表附属表格，表头、数据与汇总单元格均保留单行。名称与编号、状态与阶段等信息横向展示，需要时拆列。操作按钮、选择控件保留完整可用宽度，窄屏由表内横向滚动承载。
- 默认按当前页表头、数据、汇总的最长实际显示内容测量列宽。表格铺满容器，剩余宽度按内容宽度比例分配。文本自动宽度连同单元格留白最多 360px，超出后省略并在悬停时展示全文。操作和选择列按实际控件宽度计算。
- 使用 `AppReportTable` 的列边界拖动调宽。分页、数据或可见性变化后重新测量，用户拖动过的列保留手动宽度。避免内层固定宽度和重复截断影响拖动与全文悬停。改表格时覆盖长文本、多状态、错误提示及窄屏。

## 测试与构建

日常修改按影响范围执行定向测试；下列路径替换为实际受影响的测试文件或用例。

```bash
# 项目根目录：单个后端用例
./.venv/bin/python -m pytest backend/tests/test_operations.py::test_log_instance_filter_applies_before_scan_budget
```

涉及 PostgreSQL 的测试使用显式启用的隔离测试库；账号和夹具要求见 [`backend/tests/postgres_fixtures.py`](../../backend/tests/postgres_fixtures.py)。不要把测试指向生产库。

```bash
# frontend/：单个前端用例
pnpm test tests/operations-browser.spec.mjs -t 'incident log links'

# frontend/：类型检查
pnpm run typecheck

# frontend/：正式构建，包含完整前端测试
pnpm run build
```

`pnpm run build` 依次执行类型检查、前端测试、ECharts 资源同步和 Vite 打包，失败即停止。Frontend CI 分步执行 ESLint、类型检查、Vitest 和打包。Backend Tests 在隔离 PostgreSQL 18 和 MySQL 8.4 上运行完整后端测试。事务、租约、并发等用例及断言应保留。若将耗时压力测试分层，须保留独立的自动化检查。API contracts CI 另检查隔离 PostgreSQL 响应契约和生成类型漂移，执行要求见[契约检查](../design/API契约与迁移约定.md#契约检查)。

修改接口模型后按 [API 契约说明](../design/API契约与迁移约定.md#生成类型) 更新生成类型并验证受影响的响应及调用。

## 依赖维护

后端以 `backend/pyproject.toml` 和 `backend/uv.lock` 为唯一依赖清单与锁文件，不维护 requirements 导出文件。项目根目录的 `.venv` 是专用开发环境，显式设置 `UV_PROJECT_ENVIRONMENT`，避免与 uv 默认的 `backend/.venv` 混用。

```bash
uv lock --project backend
uv lock --project backend --check
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project backend --locked --no-build
```

主动升级单个依赖使用 `uv lock --project backend --upgrade-package 包名`；清单与锁文件一起提交。开发包含 dev 依赖组，生产安装使用 `--no-dev --extra sqlserver`。安装加 `--no-build`，缺少当前平台的二进制包时明确失败。麒麟 Python 3.11/3.12 的平台要求见 [原生部署说明](../deployment/KYLIN原生部署说明.md)。

前端只使用 pnpm 和 `frontend/pnpm-lock.yaml`。新增、移除依赖使用 `pnpm add` / `pnpm remove`，连同清单和锁文件提交；日常安装使用 `pnpm install --frozen-lockfile`。上面的 npm 命令只用于安装 pnpm 工具，不在项目中生成另一份锁文件。

CI 与 Docker 使用固定工具版本和锁文件。镜像基础依赖与离线更新校验见 [Docker 部署说明](../../README-DOCKER.md#离线部署)；依赖、Python 版本或锁文件变化时，重新构建配套运行镜像。
