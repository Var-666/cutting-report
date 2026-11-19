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

## 前端页面布局

- 普通页面复用 `page-heading`、`page-heading__title` 和 `page-heading__actions`：桌面标题 28px、窄屏 22px，标题和辅助操作按可用宽度换行。门户插画及报表头图保留独立构图；继续使用既有主题变量、间距和圆角。
- `StandardReportQuery` 按查询容器宽度排列 40px 控件；`ReportTrendPanel` 在窄屏保留时间范围和更新提示。`HomeKpiCard` 区分真实零值、加载骨架和无数据，失败和快照状态由页面反馈说明。`AppReportTable` 允许分页换行，表头、数据和汇总仍保持单行。

## 测试与构建

日常修改按影响范围执行定向测试；下列路径替换为实际受影响的测试文件或用例。

```bash
# 项目根目录：单个后端用例
./.venv/bin/python -m pytest backend/tests/test_operations.py::test_log_instance_filter_applies_before_scan_budget
```

涉及 PostgreSQL 的测试使用显式启用的隔离测试库；账号和夹具要求见 [`backend/tests/postgres_fixtures.py`](../../backend/tests/postgres_fixtures.py)。不要把测试指向生产库。

```bash
# frontend/：单个前端用例
node --import tsx --test --test-name-pattern='incident log links' tests/operations-browser.test.mjs

# frontend/：类型检查
pnpm run typecheck

# frontend/：正式构建，包含完整前端测试
pnpm run build
```

`pnpm run build` 依次执行类型检查、前端测试、ECharts 资源同步和 Vite 打包，失败即停止。GitHub 的 Frontend CI 使用同一构建命令；API contracts CI 另检查隔离 PostgreSQL 响应契约和生成类型漂移。

修改接口模型后，在 `frontend/` 执行 `pnpm run api:generate` 与 `pnpm run api:check`。生成脚本优先使用显式 `PYTHON`，否则探测根目录 `.venv`、`backend/.venv` 中可用的契约依赖，最后使用系统 Python；不启动应用或连接数据源。契约范围与维护规则见 [API 契约说明](../design/API契约与迁移约定.md)。

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
