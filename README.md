# 切割生产智能产报系统

面向内网的生产报表系统，提供领导、车间、设备报表，工人任务确认、智能分析与告警、报表导出、邮件自动化和切割文件派发。前端使用 Vue 3，后端使用 FastAPI；生产数据来自 SQL Server，SQLite 保存应用状态。

## 快速启动（Docker）

需要 Docker 与 Compose。首次部署在项目根目录执行：

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

示例配置使用演示数据；接入生产库前按 [配置参考](docs/deployment/配置参考.md) 设置数据源和只读连接。已有 `.env` 时保留原文件，不重新复制。构建需要联网，离线环境见 [离线 Docker 部署](docs/deployment/OFFLINE_DOCKER部署说明.md)。

打开 [http://127.0.0.1:8088](http://127.0.0.1:8088)，首次登录创建本地管理员。Compose 同时启动 Web 与 Worker，后台报表和自动化依赖独立 Worker。

```bash
docker compose logs -f app worker
```

## 本地开发

默认使用 Python 3.12.14、Node.js >=22.18、pnpm 10.34.9；原生麒麟支持 Python 3.11/3.12，同一解释器/平台由统一锁文件确定依赖。以下为 macOS/Linux 命令；麒麟安装步骤见下方部署文档。

```bash
cp .env.example .env
python3.12 -m venv .venv
./.venv/bin/python -m pip install --require-hashes --no-deps --only-binary=:all: -r backend/requirements-dev.txt
npm install --global pnpm@10.34.9
cd frontend
pnpm install --frozen-lockfile
cd ..
```

已有 `.env` 时跳过复制。分别在三个终端启动：

```bash
# 终端一：项目根目录，Web API
./.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

```bash
# 终端二：项目根目录，后台任务
PYTHONPATH=backend ./.venv/bin/python -m app.worker
```

```bash
# 终端三：前端
cd frontend
pnpm run dev
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)，Vite 代理 API 和报告请求。SQL Server 驱动、可选模型、能管、文件服务及 PDF 浏览器依赖见 [配置参考](docs/deployment/配置参考.md)。

本机连接真实 SQL Server 时，另执行 `./.venv/bin/python -m pip install --require-hashes --no-deps --only-binary=:all: -r backend/requirements-sqlserver.txt`，并安装操作系统对应的 Microsoft ODBC Driver 18；macOS 按[微软官方步骤](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/install-microsoft-odbc-driver-sql-server-macos)安装，Homebrew 会带入 unixODBC。可用 `./.venv/bin/python -c "import pyodbc; print(pyodbc.drivers())"` 检查驱动能否实际加载。Docker 镜像已安装上述两层依赖。未提供连接参数时需在网页数据库配置中保存真实连接，不能将空报表当作连接成功。

## 前端依赖管理

前端统一使用 pnpm，`frontend/package.json` 的 `packageManager` 固定版本，`frontend/pnpm-lock.yaml` 是唯一前端锁文件。首次安装 pnpm 的 npm 命令仅用于安装工具；日常开发使用 `pnpm install --frozen-lockfile`、`pnpm run dev` 和 `pnpm run build`，不要再运行 npm install 或维护 package-lock.json。主动修改依赖使用 `pnpm add` / `pnpm remove`，连同清单和锁文件一起提交。

CI 和 Docker 均使用相同 pnpm 版本及冻结锁文件安装。`frontend/pnpm-workspace.yaml` 仅允许现有 esbuild、fsevents、vue-demi 安装脚本。前端已构建进部署包，运行服务器无需安装 pnpm；Docker 仅在构建阶段使用。

## Python 依赖锁定

`backend/pyproject.toml` 声明直接依赖、开发依赖与 SQL Server 可选依赖，`backend/uv.lock` 固定完整依赖树和包哈希。三份 `requirements*.txt` 均由锁文件生成，不手改；Docker、原生安装和 CI 使用带哈希的清单，不在部署时重新解析版本，也不自动升级 pip。生产清单不包含 pytest。

维护依赖时使用固定工具 `uv==0.13.6`，例如在专用工具环境安装后执行：

```sh
uv lock --project backend
python scripts/lock_python_dependencies.py
python scripts/lock_python_dependencies.py --check
```

普通安装只消费已提交的清单，无需在服务器安装 uv。可选的 uv 开发安装为 `UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project backend --locked`，会同步/清理该环境，请先停止使用旧环境并保留必要资料；不要指向共享虚拟环境。默认 Python 版本在 `backend/.python-version`。主动升级单个依赖使用 `uv lock --project backend --upgrade-package 包名`，重新导出、验证后连同锁文件提交。CI 会拒绝清单与锁文件不一致的变更。

Docker 的 Python/Node 基础镜像使用摘要固定，Debian 系统包使用日期快照，ODBC 固定版本，Chromium 随已锁定的 Playwright 版本安装。锁定不等于永不更新：安全更新必须主动刷新依赖/镜像/系统快照并重新构建验证。

离线源码挂载会校验镜像中的依赖指纹；清单变化或旧镜像没有指纹时，需要重新构建、导入配套镜像，不能仅替换源码。无依赖变动的业务更新仍可复用配套镜像。原生主机的 ODBC、字体和操作系统由现场维护；锁文件保证 Python 包版本，不承诺不同 OS/架构生成逐字节相同的镜像。

## 构建与部署

生产式本地运行先在 `frontend/` 执行 `pnpm run build`，再启动一个 Web 和一个 Worker；Web 去掉 `--reload`。前端构建依次强制执行 `pnpm run typecheck`、`pnpm test`，通过后才同步 ECharts 资源并打包；任一步失败都会停止构建。日常修改只运行受影响的定向测试，正式构建执行这一次完整前端验证。正式环境将两个进程注册为独立服务。GitHub Actions 在 push 和 pull request 时，使用 Node.js 22、`pnpm install --frozen-lockfile` 和同一 `pnpm run build` 命令自动验证前端。

- [Docker 部署与更新](README-DOCKER.md)
- [麒麟原生部署](docs/deployment/KYLIN原生部署说明.md)
- [Web/Worker 升级与回滚](docs/deployment/WEB_WORKER进程隔离升级说明.md)
- [多设备数据迁移](docs/project/multi-device-isolation.md)

升级已有系统前按对应文档备份并核对迁移条件，保留原配置与数据卷。

车间、文件派发和告警已采用 Pydantic/OpenAPI 生成前端类型，前端使用 Node.js >=22.18。在 `frontend/` 运行 `pnpm run api:generate` 更新 `src/api/generated/`，运行 `pnpm run api:check` 检查是否过期。生成脚本使用根目录 `.venv`，也可通过 `PYTHON` 指定已安装后端依赖的解释器；不启动 Web/Worker、不连接数据库。修改对应后端模型后重新生成，不手改生成文件；API contracts CI 会检查漂移。其他接口目前仍沿用既有类型维护方式。

SQLite 结构升级统一登记在 `backend/app/database/migrations/registry.py`，001–013 接管既有结构；后续新增编号迁移，禁止继续把 ALTER TABLE 放入仓储初始化。已有库升级前自动通过 SQLite Backup API 写入 `data/backups/*-before-schema-*.db`，待执行批次及版本标记在同一事务提交，失败整体回滚。原设备归属迁移及其备份规则保留。

手工检查：`./.venv/bin/python scripts/migrate_database.py status --database /absolute/path/data/metadata.db`；手工升级将 `status` 改为 `upgrade`，执行前停止 Web 和 Worker。命令拒绝活动 Web 锁和未释放的 Worker 租约。迁移失败后保留备份与错误、修复原因后重试；若升级后已接收业务，不直接覆盖旧备份，以免丢失新确认和审计。确需恢复时，停止全部进程、先保全当前库，再通过 SQLite Backup API 恢复备份，启动与该备份匹配的版本；不要在线仅覆盖 `.db` 而混用旧 WAL/SHM。

车间路由只负责 HTTP、角色依赖和错误映射，业务由 `WorkshopService` 组织。用户、会话、日报、确认分别使用 `UserRepository`、`SessionRepository`、`WorkshopReportRepository`、`ConfirmationRepository`；原 `WorkshopRepository` 保留为嵌入式调用的兼容门面，新实现使用独立仓储。确认冲突、修订号和设备范围仍在数据库事务内检查。

核心 API 响应契约测试：`./.venv/bin/python -m pytest backend/tests/test_frontend_response_contracts.py`，检查切割报表、焊接各类报表/详情及认证响应的关键字段；使用隔离数据，不连接生产库。这些测试覆盖关键字段，并非完整的响应 Schema 校验；接口变更时应同步维护前端类型与相关断言。焊接 API 查询要求 `start/end` 同时存在，页面草稿独立使用 `WeldingFilterDraft`，提交时复用日期区间校验。

## 文档

- [配置参考](docs/deployment/配置参考.md)：来源优先级、参数、生效方式和诊断。
- [功能与接口参考](docs/project/功能与接口参考.md)：报表口径、能耗、账号权限、文件派发及导出。
- [当前进度](docs/project/progress.md)：当前状态、近期成果、问题和下一步。
- [历史变更记录](docs/project/changelog.md)：已完成变更和当时验证结果。
- [开发约定与架构](docs/project/README.md) · [完整文档目录](docs/README.md)

开发时遵循 [AGENTS.md](AGENTS.md)，按实际影响范围执行定向测试；不默认运行全量套件。环境配置、凭据及运行数据不得提交 Git。
