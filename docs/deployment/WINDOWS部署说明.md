# Windows 内网部署说明

推荐在已经安装 Docker 的 Windows 内网机上使用 Docker 部署。这样 Node.js、Python 和 SQL Server ODBC 依赖都由镜像统一管理。部署包同时包含原生 Python 启动脚本，作为 Docker 不可用时的备用方案。

2026-09-01 版已加入统一设备能耗报表：同一页面包含当日任务能耗表和月度每日汇总表，
桌面端均为左图右表。升级、能管配置和一键启动见项目根目录
[`README-DOCKER.md`](../../README-DOCKER.md)，已有系统务必保留原 `.env` 和数据卷。

## 一、推荐方式：Docker 部署

### 1. 解压并配置

将压缩包解压到固定目录，例如 `D:\CuttingReport`，然后在 PowerShell 中进入该目录：

```powershell
cd D:\CuttingReport
Copy-Item .env.example .env
notepad .env
```

上面的复制命令仅用于首次部署；已有 `.env` 时只编辑，不要用模板覆盖它。

至少修改以下配置：

```env
APP_DEMO_MODE=false
APP_DATA_SOURCE_MODE=sqlserver

SQLSERVER_HOST=10.0.0.20
SQLSERVER_PORT=1433
SQLSERVER_DATABASE=生产数据库名
SQLSERVER_USERNAME=只读账号
SQLSERVER_PASSWORD=正式密码
SQLSERVER_DRIVER=ODBC Driver 18 for SQL Server
SQLSERVER_ENCRYPT=yes
SQLSERVER_TRUST_SERVER_CERTIFICATE=yes
SQLSERVER_QUERY_TIMEOUT_SECONDS=120
LEADERSHIP_REPORT_CACHE_SECONDS=300
LEADERSHIP_REPORT_HISTORY_CACHE_SECONDS=86400
LEADERSHIP_REPORT_RECONCILE_HOUR=2
REPORT_DATA_CACHE_SECONDS=300

WORKER_DATABASE_AUTH_ENABLED=true
WORKER_DATABASE_AVAILABLE_VALUE=1
WORKER_DATABASE_UNLOCKED_VALUE=0

WORKER_SQLSERVER_HOST=10.0.0.30
WORKER_SQLSERVER_PORT=1433
WORKER_SQLSERVER_DATABASE=HR数据库名
WORKER_SQLSERVER_USERNAME=HR只读账号
WORKER_SQLSERVER_PASSWORD=HR只读密码

FILE_DATABASE_ENABLED=false
FILE_SQLSERVER_HOST=10.0.0.40
FILE_SQLSERVER_PORT=1433
FILE_SQLSERVER_DATABASE=PDM文件数据库名
FILE_SQLSERVER_USERNAME=PDM只读账号
FILE_SQLSERVER_PASSWORD=PDM只读密码

# 允许同一内网其他电脑访问；只允许服务器本机访问则写 127.0.0.1
DOCKER_BIND_ADDRESS=0.0.0.0
DOCKER_PORT=8088
```

### 2. 构建并启动

确保 Docker Desktop 已启动并使用 Linux containers，然后执行：

```powershell
docker compose up -d --build
```

第一次构建需要下载 Node、Python 和 Microsoft ODBC 基础依赖。构建成功后，后续启动不需要再次下载。

如果这台内网 Windows 不能访问互联网，仅传本源码 ZIP 不足以完成第一次镜像构建，因为 Docker 仍需下载基础镜像、npm、pip 和 ODBC 安装包。应在一台可联网且支持 Docker Buildx 的机器上额外准备 Windows 服务器可用的 AMD64 镜像：

```powershell
docker buildx build --platform linux/amd64 -t cutting-report:local --load .
docker save -o cutting-report-image-amd64.tar cutting-report:local
```

把 `cutting-report-image-amd64.tar` 一并传到内网 Windows 后执行：

```powershell
docker load -i cutting-report-image-amd64.tar
docker compose up -d --no-build
```

检查状态和日志：

```powershell
docker compose ps
docker compose logs --tail 100 app
```

健康状态应为 `healthy`。浏览器访问：

```text
http://Windows服务器IP:8088
```

管理员也可在“设置 → 数据库配置”中修改三套数据库参数。界面会写入持久化卷中的 `data/settings.env`，不会覆盖部署目录原有的 `.env`；密码不会在页面回显。保存后执行以下命令重启即可生效：

```powershell
docker compose restart app
```

注意：上述 `restart` 只适用于界面保存到持久化卷中的 `data/settings.env`。
若修改宿主机 `.env`（例如新增能管账号），执行 `docker compose up -d --force-recreate app`
重新创建容器才能读取新环境变量；程序升级仍使用下面带 `--build` 的命令。

停止服务：

```powershell
docker compose down
```

更新程序时，先覆盖程序文件但保留 `.env`，然后重新执行：

```powershell
docker compose up -d --build
```

`docker compose down` 不会删除系统数据。不要执行 `docker compose down -v`，因为 `-v` 会删除管理员、确认记录和已生成报表所在的数据卷。

### 3. Docker 访问 SQL Server

不要把 `SQLSERVER_HOST` 配成 `127.0.0.1`，除非 SQL Server 也运行在同一个容器中。应填写容器能够访问的内网数据库 IP 或 DNS 名称。

如果 SQL Server 就安装在这台 Windows 宿主机上，可以优先尝试：

```env
SQLSERVER_HOST=host.docker.internal
```

并确保 SQL Server 已启用 TCP/IP、使用固定端口，同时允许 Docker 网络访问。

## 二、备用方式：原生 Python 部署

### 1. 服务器准备

建议使用 64 位 Windows Server 或 Windows 10/11，并安装：

1. Python 3.11 或 3.12（64 位），安装时勾选“Add Python to PATH”；
2. Microsoft ODBC Driver 18 for SQL Server（64 位）；
3. 确保服务器可以访问生产 SQL Server 的 TCP 端口（通常为 1433）。

可在 PowerShell 中测试：

```powershell
Test-NetConnection 数据库IP -Port 1433
```

### 2. 解压和安装

1. 将压缩包解压到固定目录，例如 `D:\CuttingReport`；
2. 双击 `scripts\windows\install_windows.bat`；
3. 安装脚本会创建 `.venv`、安装 Python 依赖，并在不存在时由 `.env.example` 创建 `.env`；
4. 用记事本打开 `.env`，填写正式数据库连接参数。

正式数据库的最低配置示例：

```env
APP_ENV=production
APP_DEMO_MODE=false
APP_DATA_SOURCE_MODE=sqlserver
APP_HOST=0.0.0.0
APP_PORT=8000
APP_CORS_ORIGINS=
AUTH_COOKIE_SECURE=false

SQLSERVER_HOST=10.0.0.20
SQLSERVER_PORT=1433
SQLSERVER_DATABASE=生产数据库名
SQLSERVER_USERNAME=只读账号
SQLSERVER_PASSWORD=正式密码
SQLSERVER_DRIVER=ODBC Driver 18 for SQL Server
SQLSERVER_ENCRYPT=yes
SQLSERVER_TRUST_SERVER_CERTIFICATE=yes

WORKER_DATABASE_AUTH_ENABLED=true
WORKER_DATABASE_AVAILABLE_VALUE=1
WORKER_DATABASE_UNLOCKED_VALUE=0
```

三套账号分别只授予各自业务对象的 `SELECT` 权限：

- 生产库 `dbo.task`：领导、车间、设备三张报表；
- 工人库 `dbo.HRbase_userInfo`：工人登录；
- 文件库 `PDM_DesignDocumentInfo` / `PDM_DocumentFileInfo`：切割文件查询（启用前需确认实际 schema 和字段映射）。

系统已将生产库、工人库和文件库拆分为三套连接。为兼容旧部署，`WORKER_SQLSERVER_*` 或 `FILE_SQLSERVER_*` 全部留空时会回退到生产库；正式上线不应依赖此回退，可在“设置 → 数据库配置”检查。

不要把配置完成后的 `.env` 发送给其他人或提交到代码仓库。

### 3. 启动和访问

双击：

```text
scripts\windows\start_windows.bat
```

服务器本机访问：

```text
http://127.0.0.1:8000
```

同一内网的其他电脑访问：

```text
http://Windows服务器IP:8000
```

如果其他电脑无法打开，需要由管理员在 Windows 防火墙中放行 TCP 8000 端口。PowerShell 示例：

```powershell
New-NetFirewallRule -DisplayName "Cutting Report 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

首次打开系统时，根据页面提示创建本地管理员账号。工人使用 HR 数据库中的“公司工号 + 密码”登录。

## 三、验证数据库模式

启动后访问：

```text
http://127.0.0.1:8000/api/v1/health
```

正常情况下应看到：

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "data_source_mode": "sqlserver",
    "data_source_name": "SQL Server"
  }
}
```

管理员登录后还可以进入“数据库管理”执行连接测试和三类数据预览。

## 四、原生方式长期运行

直接关闭 `scripts\windows\start_windows.bat` 的命令窗口会停止服务。正式使用时，建议由运维人员使用 NSSM、WinSW 或 Windows 任务计划程序将下面的命令注册为开机启动服务，并确保工作目录是解压目录：

```text
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --workers 1
```

必须保持 `--workers 1`，因为当前系统使用本地 SQLite 保存管理员、会话、日报快照和工人确认记录。

生产环境建议在 IIS/Nginx 前配置 HTTPS。启用 HTTPS 后将 `.env` 中的 `AUTH_COOKIE_SECURE` 改为 `true`。

## 五、数据和备份

原生 Python 方式的以下目录会在运行时自动创建：

- `data`：管理员、会话、日报快照、确认记录等本地数据；
- `reports`：生成的 HTML 报表；
- `logs`：运行日志。

Docker 方式的数据保存在 `cutting_data`、`cutting_reports` 和 `cutting_logs` 命名卷中。无论采用哪种方式，升级前都应备份数据；新版本覆盖程序文件时不要删除 `.env`。

## PDF 导出升级

三个报表的“导出报表”下拉框可选择 HTML 或 PDF，历史列表也可下载 PDF。
Docker 部署使用新版镜像，镜像内已包含 Chromium 和中文字体。
原生 Python 部署请重新运行 `scripts\windows\install_windows.bat`，或在更新依赖后，
用启动服务的同一 Windows 账号执行 `.venv\Scripts\python.exe -m playwright install chromium`。
内网离线部署需要提前准备浏览器文件或完整镜像，不能仅复制 Python 源码。
