# 切割生产智能产报系统

面向内网的生产报表系统，提供领导、车间、设备报表，工人任务确认、智能分析与告警、报表导出、邮件自动化和切割文件派发。前端使用 Vue 3，后端使用 FastAPI；生产数据来自 SQL Server/MySQL，PostgreSQL 保存应用状态。

## 快速启动（Docker）

需要 Docker 与 Compose。首次部署在项目根目录执行：

```bash
cp .env.example .env
docker compose build app
docker compose up -d postgres
docker compose run --rm --no-deps migrate python scripts/manage_postgres.py schema
docker compose up -d app scheduler worker automation
docker compose ps
```

示例配置使用演示数据；接入生产库前按 [配置参考](docs/deployment/配置参考.md) 设置数据源和只读连接。已有 `.env` 时保留原文件，不重新复制。构建需要联网，离线环境见 [离线 Docker 部署](docs/deployment/OFFLINE_DOCKER部署说明.md)。

打开 [http://127.0.0.1:8088](http://127.0.0.1:8088)，首次登录创建本地管理员。Compose 启动 Web、Scheduler、Consumer和Automation，Consumer默认1个、按需同机扩容，三个后台角色通过PostgreSQL独立协调。

```bash
docker compose logs -f app scheduler worker automation
```

## 本地开发

默认使用 Python 3.12.14、uv 0.13.6、Node.js >=22.18、pnpm 10.34.9；原生麒麟支持 Python 3.11/3.12，同一解释器/平台由统一锁文件确定依赖。以下为 macOS/Linux 命令；麒麟安装步骤见下方部署文档。先按 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装固定版本 0.13.6。已有 `.venv` 时先确认其归属和 Python 版本；uv 会同步移除额外依赖，不要指向共享环境，旧环境需保留时先另存再安装。

首次安装 uv（已安装 0.13.6 时跳过）：

```bash
curl -LsSf https://astral.sh/uv/0.13.6/install.sh -o /tmp/cutting-uv-install.sh
sh /tmp/cutting-uv-install.sh
export PATH="$HOME/.local/bin:$PATH"
```

```bash
cp .env.example .env
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project backend --locked --no-build
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

本机连接真实 SQL Server 时，另执行 `UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project backend --locked --extra sqlserver --no-build`，后续同步也需保留 `--extra sqlserver`，并安装操作系统对应的 Microsoft ODBC Driver 18；macOS 按[微软官方步骤](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/install-microsoft-odbc-driver-sql-server-macos)安装，Homebrew 会带入 unixODBC。可用 `./.venv/bin/python -c "import pyodbc; print(pyodbc.drivers())"` 检查驱动能否实际加载。Docker 镜像已安装上述两层依赖。未提供连接参数时需在网页数据库配置中保存真实连接，不能将空报表当作连接成功。

## 前端依赖管理

前端统一使用 pnpm，`frontend/package.json` 的 `packageManager` 固定版本，`frontend/pnpm-lock.yaml` 是唯一前端锁文件。首次安装 pnpm 的 npm 命令仅用于安装工具；日常开发使用 `pnpm install --frozen-lockfile`、`pnpm run dev` 和 `pnpm run build`，不要再运行 npm install 或维护 package-lock.json。主动修改依赖使用 `pnpm add` / `pnpm remove`，连同清单和锁文件一起提交。

CI 和 Docker 均使用相同 pnpm 版本及冻结锁文件安装。`frontend/pnpm-workspace.yaml` 仅允许现有 esbuild、fsevents、vue-demi 安装脚本。前端已构建进部署包，运行服务器无需安装 pnpm；Docker 仅在构建阶段使用。

## Python 依赖锁定

`backend/pyproject.toml` 声明直接依赖、开发依赖与 SQL Server 可选依赖，`backend/uv.lock` 固定完整依赖树和包哈希。开发、Docker、麒麟原生安装和 CI 统一使用 uv 0.13.6 的 `uv sync --locked`，不再生成或维护 requirements 导出文件。清单与锁不一致会报错，不会在部署时重写锁文件或升级版本。

项目根目录的 `.venv` 是开发环境，需显式设置 `UV_PROJECT_ENVIRONMENT="$PWD/.venv"`，避免 uv 默认的 backend/.venv 与现有启动脚本不一致。开发和 CI 包含 dev 依赖组；生产使用 `--no-dev --extra sqlserver`。安装统一加 `--no-build`，只接受锁定的二进制包；缺少当前平台的 wheel 时明确失败。

维护依赖时更新并提交清单与锁文件：

```sh
uv lock --project backend
uv lock --project backend --check
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project backend --locked --no-build
```

主动升级单个依赖使用 `uv lock --project backend --upgrade-package 包名`，验证后连同清单提交。默认 Python 版本在 `backend/.python-version`。麒麟安装脚本要求预先安装 uv 0.13.6（可通过 `UV_BIN` 指定离线二进制），使用现场 Python 3.11/3.12 或已有兼容 `.venv`，禁止自动下载 Python。Docker 仅在构建阶段使用固定摘要的 uv 镜像，运行容器和 Docker 宿主机不需要安装 uv。

Docker 的 Python/Node 基础镜像使用摘要固定，Debian 系统包使用日期快照，ODBC 固定版本，Chromium 随已锁定的 Playwright 版本安装。锁定不等于永不更新：安全更新必须主动刷新依赖/镜像/系统快照并重新构建验证。

离线源码挂载会校验 `pyproject.toml`、`uv.lock`、`.python-version` 三份文件与镜像中的指纹。任一文件变化或旧镜像没有新指纹时，需要重新构建、导入配套镜像，不能仅替换源码；升级预检会在替换 Web/Worker 前拒绝旧镜像。无依赖变动的业务更新仍可复用配套镜像。原生主机的 ODBC、字体和操作系统由现场维护；锁文件保证 Python 包版本，不承诺不同 OS/架构生成逐字节相同的镜像。

## 构建与部署

生产式本地运行先在 `frontend/` 执行 `pnpm run build`，再启动一个 Web、一个Scheduler、默认一个、可同机扩容的Consumer和一个Automation；Web 去掉 `--reload`。前端构建依次强制执行 `pnpm run typecheck`、`pnpm test`，通过后才同步 ECharts 资源并打包；任一步失败都会停止构建。日常修改只运行受影响的定向测试，正式构建执行这一次完整前端验证。正式环境将四个应用进程注册为独立服务。GitHub Actions 在 push 和 pull request 时，使用 Node.js 22、`pnpm install --frozen-lockfile` 和同一 `pnpm run build` 命令自动验证前端。

- [Docker 部署与更新](README-DOCKER.md)
- [麒麟原生部署](docs/deployment/KYLIN原生部署说明.md)
- [Web/Worker 升级与回滚](docs/deployment/WEB_WORKER进程隔离升级说明.md)

升级已有系统前按对应文档备份并核对迁移条件，保留原配置与数据卷。

车间、文件派发和告警已采用 Pydantic/OpenAPI 生成前端类型，前端使用 Node.js >=22.18。在 `frontend/` 运行 `pnpm run api:generate` 更新 `src/api/generated/`，运行 `pnpm run api:check` 检查是否过期。生成脚本优先使用显式 `PYTHON`，否则依次探测根目录 `.venv`、`backend/.venv`，选择能加载契约导出依赖的环境，均不可用时使用系统 Python；不启动 Web/Worker、不连接数据库。修改对应后端模型后重新生成，不手改生成文件；API contracts CI 会检查漂移。其他接口目前仍沿用既有类型维护方式。

应用状态结构由 `backend/app/database/postgres_migrations/` 的追加编号迁移管理。Web/Worker 使用 cutting_app 校验现成 Schema，只有独立维护命令使用 cutting_migrator 升级；正常启动不执行 DDL。

当前版本仅维护PostgreSQL，不包含旧SQLite升级或导入工具。结构维护、账号、备份及回退见[状态库维护](docs/deployment/PostgreSQL状态库迁移.md)。

车间路由只负责 HTTP、角色依赖和错误映射，业务由 `WorkshopService` 组织。用户、会话、日报、确认分别使用 `UserRepository`、`SessionRepository`、`WorkshopReportRepository`、`ConfirmationRepository`；原 `WorkshopRepository` 保留为嵌入式调用的兼容门面，新实现使用独立仓储。确认冲突、修订号和设备范围仍在数据库事务内检查。

核心 API 响应契约测试：`./.venv/bin/python -m pytest backend/tests/test_frontend_response_contracts.py`，检查切割报表、焊接各类报表/详情及认证响应的关键字段；使用隔离数据，不连接生产库。这些测试覆盖关键字段，并非完整的响应 Schema 校验；接口变更时应同步维护前端类型与相关断言。焊接 API 查询要求 `start/end` 同时存在，页面草稿独立使用 `WeldingFilterDraft`，提交时复用日期区间校验。

## 文档

- [配置参考](docs/deployment/配置参考.md)：来源优先级、参数、生效方式和诊断。
- [功能与接口参考](docs/project/功能与接口参考.md)：报表口径、能耗、账号权限、文件派发及导出。
- [当前进度](docs/project/progress.md)：当前状态、近期成果、问题和下一步。
- [开发规范](AGENTS.md) · [当前架构](docs/project/architecture.md) · [完整文档目录](docs/README.md)

开发时遵循 [AGENTS.md](AGENTS.md)，按实际影响范围执行定向测试；不默认运行全量套件。环境配置、凭据及运行数据不得提交 Git。

## 通用外部数据源（P13）

管理员在“系统设置 → 数据库配置 → 其他数据源”登记 SQL Server 或 MySQL。新增和编辑共用表单，支持连接测试、启停和删除。旧生产/HR/PDM/焊接配置继续保留，PostgreSQL 状态库不纳入此管理区域。

例如登记下一个 SQL Server 数据库：

1. 点击“新增数据源”，ID 填 `quality`，名称填“质量检测数据库”，选择 SQL Server，填写地址、端口、数据库及只读账号密码。
2. 点击“测试连接”：只执行 `SELECT 1`，不保存参数，也不验证业务表和统计口径。确认后点击“保存”。编辑时密码留空保留最新已保存密码。
3. 新业务在 Repository 中取得该数据源的客户端，使用参数化 SQL；Service 显式接收 Repository。以下仅为接入示例，本次不创建质量检测业务：

   ```python
   from app.database.source_registry import DatabaseSourceRegistry
   from app.database.sqlserver import SqlServerClient

   class QualityRepository:
       def __init__(self, sources: DatabaseSourceRegistry):
           client = sources.client('quality')
           if not isinstance(client, SqlServerClient):
               raise ValueError('quality 必须是 SQL Server 数据源')
           self.client = client

       # 在此实现业务 SQL；SQL Server 参数使用 ?，MySQL 会话参数使用 %s。
   ```

4. 在业务操作的装配入口，把当前快照的 Registry 传给 Repository，再把 Repository 传给业务 Service；Web 从 `runtime_state(request).database_sources`、Worker 从 `database_context(runtime).database_sources` 选取。固定本次操作的客户端，不跨操作长期保留启动时的 Repository。此方式无需修改公共 Runtime 或连接层；专属业务的长期服务装配仍遵循现有类型化 Runtime 约定。
5. 在统一产线目录的 source_key 或 `database_runtime.source_bindings` 登记 明确业务引用。部署消费者前先登记对应数据源；存在引用时禁止删除或更换数据库类型。移除业务引用后才能删除。

以后新增连接无需重复开发配置表单、密码保存、连接测试、连接池或公共客户端。新业务仍需编写自己的 Repository、Service 和必要的 API；新增连接不会自动获得 Workbench 权限，也不会新增生产线或空白报表。

新增源配置以 JSON 存在 `data/settings.env` 的 `DATABASE_SOURCES_JSON` 一项，只有这一份持久化来源；沿用文件锁、0600 权限和原子替换。Web 保存后立即发布，Worker 下一操作读取同一文件；配置无效或读取失败保留旧快照。诊断页对这项配置只展示状态和进程一致性，不显示 JSON/密码。旧生产连接和设备目录须先由 `scripts/migrate_production_lines.py` 迁移；正式运行只读取统一产线及来源配置。

未变化的客户端在快照之间复用。旧请求/Repository 保留旧配置，最后一个引用释放后 MySQL 客户端清理旧池；主动关闭时等待在途会话返回后清理。连接池清理封装使用当前 Connector/Python 的 `_remove_connections`，升级驱动时需执行池生命周期定向测试。MySQL 保持最多 4 个连接、只读事务和 REPEATABLE READ；不同源各自拥有连接资源。

P13 的隔离验证命令：

```bash
./.venv/bin/python -m pytest backend/tests/test_database_sources.py -q
cd frontend
node --import tsx --test tests/database-sources.test.mjs
pnpm run api:check
pnpm run typecheck
```

现场网络、业务表权限和查询性能仍需在内网部署后另行验证，勿将连接测试成功当作业务验收。


## BM 工程与船名关联

在“数据库配置 → 其他数据源”注册 ID 为 **`bm`** 的 SQL Server 数据源并启用。数据库名称按现场实际填写（当前为 `bm`），账号需要读取 `dbo.BM_PROJECT` 的 `ProjectNum`、`ProjectName`。IP、账号和密码仅填管理页，不写入源码。测试连接成功后保存，新 Web 请求与 Worker 操作即使用该连接，无需新增产线或重启。

旧 SQL Server 出现 `legacy sigalg disallowed or unsupported` 且无法更新服务器时，可在该数据源编辑框勾选“兼容旧版 SQL Server TLS”，先测试连接再保存。`legacy_tls` 默认关闭；启用后仅该数据源的批量只读查询使用一次性子进程和专用 OpenSSL 配置，允许 TLS 1.0 与弱签名算法，不修改 Web/Worker、宿主机及其他连接的 TLS 策略。密码经私有管道传递，不写入命令行、临时文件或日志；查询结束、取消或超时均回收子进程。此模式用于 BM 批量映射与 `SELECT 1` 测试，不支持直接连接或流式查询；数据库端完成 TLS/证书更新后应关闭兼容。部署需同时更新后端（包含 `sqlserver_legacy.py/.cnf`）与前端，重建 Web/Worker；现有依赖与镜像不变。

切割任务沿用原工程编号提取规则（非空 `PROJECT_NAME` 优先，否则 `PROJECT_CODE`），完整编号去首尾空格、忽略大小写后精确匹配 `ProjectNum`，船名取 `ProjectName`。不截前缀、不根据文件名或部分编号猜测；无匹配、空名称或同编号多行时船名为空，生产任务及统计仍保留。BM 查询失败记录脱敏日志，保留生产数据及已保存的船名；取消和执行期限仍向上传播。

标准切割报表页面、船名选项/筛选、HTML/PDF 导出与 AI 明细使用同一船名服务。接口继续使用 `ship_no` 字段承载标准切割报表的船名，PDM 文件及派发中的真实船号不变。车间/设备任务明细及导出的工程号、船名、分段号（分段名）三项各自缺失时统一显示“非船”，确认列表也沿用此显示；有值的字段保留原值，原始空值及任务归类保留，便于后续补号。领导报表仍按原工程号统计。演示模式不查询 BM；未登记或停用 `bm` 时保留原兼容行为与已有快照数据。

PostgreSQL日报单独保存船名字段，结构由已发布编号迁移维护。历史日报展示时也查询映射；正常同步时可补齐原空值或原工程编号占用的船名字段，不改生产任务、快照修订号或确认/审计记录。重新导出的报告使用当前映射，已生成的 HTML/PDF 文件保持原内容。

两台切割设备共享有界船名缓存，使用现有报表缓存 TTL；强制查询会刷新船名。修改 `bm` 连接会重建受影响的切割服务，旧请求保留原引用；更改其他无关数据源不会重建切割服务。`bm` 已声明为切割报表引用，登记后可停用，但不能通过管理页删除或切换数据库类型。


P1 旧配置升级须先停止旧 Web/Worker 并运行 `python scripts/migrate_production_lines.py`；状态库使用PostgreSQL，旧SQLite部署需先使用历史版本完成迁移。统一实例与来源配置、备份位置、新增切割/焊接线及回退边界见[配置参考](docs/deployment/配置参考.md#p1-升级与新增实例)。

P7 Consumer默认1个，同机扩缩容使用 `docker compose up -d --no-deps --scale worker=2 worker`（或3/1）；持久化数量设置CONSUMER_REPLICAS。原生每个服务显式设置不同CONSUMER_INSTANCE_ID。旧P6先停Consumer并追加003，再执行prepare-consumers，不能直接混跑两种租约协议。实例探针、容量预算和回退见[部署说明](docs/deployment/WEB_WORKER进程隔离升级说明.md)。
