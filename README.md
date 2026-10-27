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

需要 Python 3.12+、Node.js 20.19+。以下为 macOS/Linux 命令；麒麟安装步骤见下方部署文档。

```bash
cp .env.example .env
python3 -m venv .venv
./.venv/bin/python -m pip install -r backend/requirements-dev.txt
cd frontend
npm ci
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
npm run dev
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)，Vite 代理 API 和报告请求。SQL Server 驱动、可选模型、能管、文件服务及 PDF 浏览器依赖见 [配置参考](docs/deployment/配置参考.md)。

本机连接真实 SQL Server 时，另执行 `./.venv/bin/python -m pip install -r backend/requirements-sqlserver.txt`，并安装操作系统对应的 Microsoft ODBC Driver 18；macOS 按[微软官方步骤](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/install-microsoft-odbc-driver-sql-server-macos)安装，Homebrew 会带入 unixODBC。可用 `./.venv/bin/python -c "import pyodbc; print(pyodbc.drivers())"` 检查驱动能否实际加载。Docker 镜像已安装上述两层依赖。未提供连接参数时需在网页数据库配置中保存真实连接，不能将空报表当作连接成功。

## 构建与部署

生产式本地运行先在 `frontend/` 执行 `npm run build`，再启动一个 Web 和一个 Worker；Web 去掉 `--reload`。前端构建依次强制执行 `npm run typecheck`、`npm test`，通过后才同步 ECharts 资源并打包；任一步失败都会停止构建。日常修改只运行受影响的定向测试，正式构建执行这一次完整前端验证。正式环境将两个进程注册为独立服务。GitHub Actions 在 push 和 pull request 时，使用 Node.js 22、`npm ci` 和同一 `npm run build` 命令自动验证前端。

- [Docker 部署与更新](README-DOCKER.md)
- [麒麟原生部署](docs/deployment/KYLIN原生部署说明.md)
- [Web/Worker 升级与回滚](docs/deployment/WEB_WORKER进程隔离升级说明.md)
- [多设备数据迁移](docs/project/multi-device-isolation.md)

升级已有系统前按对应文档备份并核对迁移条件，保留原配置与数据卷。

核心 API 响应契约测试：`./.venv/bin/python -m pytest backend/tests/test_frontend_response_contracts.py`，检查切割报表、焊接各类报表/详情及认证响应的关键字段；使用隔离数据，不连接生产库。这些测试覆盖关键字段，并非完整的响应 Schema 校验；接口变更时应同步维护前端类型与相关断言。焊接 API 查询要求 `start/end` 同时存在，页面草稿独立使用 `WeldingFilterDraft`，提交时复用日期区间校验。

## 文档

- [配置参考](docs/deployment/配置参考.md)：来源优先级、参数、生效方式和诊断。
- [功能与接口参考](docs/project/功能与接口参考.md)：报表口径、能耗、账号权限、文件派发及导出。
- [当前进度](docs/project/progress.md)：当前状态、近期成果、问题和下一步。
- [历史变更记录](docs/project/changelog.md)：已完成变更和当时验证结果。
- [开发约定与架构](docs/project/README.md) · [完整文档目录](docs/README.md)

开发时遵循 [AGENTS.md](AGENTS.md)，按实际影响范围执行定向测试；不默认运行全量套件。环境配置、凭据及运行数据不得提交 Git。
