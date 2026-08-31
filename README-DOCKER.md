# Windows Docker 部署包 · 2026-08-31

本包沿用之前的 Docker Compose 部署方式，已包含最新的能耗日/月报、汇总行和两张图。
文件下载更新版还包括：按文档名和版本命名、本地持久化、下载文件下拉选择、已下载列表单选；不提供内容预览。
这是源码构建包，不含离线 Docker 镜像；第一次构建需要联网下载依赖。
Windows 上需已安装并启动 Docker，使用 Linux containers。无需单独安装 Python 或 Node.js。

## 已有旧系统：升级

1. 先备份原部署目录的 `.env`、`docker/input` 和 Docker 数据卷。
2. 将本包的程序文件覆盖到原部署目录。不要删除原 `.env`、Excel 输入文件或数据卷。
3. 按下面的说明，在原 `.env` 中补充能管配置；不要用 `.env.example` 覆盖正式配置。
4. 在原部署目录打开 CMD，运行：

```cmd
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
```

也可双击 `START_DOCKER.bat`，它不会覆盖已存在的 `.env`。
项目名仍为 `cutting-report`，沿用原来的 `cutting_data`、`cutting_reports`、`cutting_logs` 命名卷。
若旧系统使用自定义 `-p` 项目名或修改过卷配置，继续沿用原设置，不要直接启动第二套实例。
**不要执行 `docker compose down -v`，它会删除数据卷。**

## 第一次部署

解压到固定目录（例如 `D:\CuttingReport`），不要直接在 ZIP 内运行。
双击 `START_DOCKER.bat`：首次会创建 `.env` 并打开记事本；配置保存后再次双击才会构建启动。
也可在 CMD 中手动操作；以下复制命令仅用于没有 `.env` 的首次部署：

```cmd
cd /d D:\CuttingReport
if not exist .env copy .env.example .env
notepad .env
docker compose up -d --build --wait --wait-timeout 180
```

模板默认是显式演示模式。正式使用请设置 `APP_DEMO_MODE=false`，并将
`APP_DATA_SOURCE_MODE` 改为原有的 `sqlserver` 或 `excel`。
SQL Server 连接沿用现场配置；Excel 模式需自行将 `TASK.xlsx` 放入 `docker/input`。
本包不带生产 Excel、历史数据库或密码。独立能耗日/月报本身不依赖任务数据源。

默认本机地址为 `http://127.0.0.1:8000`。首次打开按页面提示创建管理员账号。
如需其他内网电脑访问，在 `.env` 中设置 `DOCKER_BIND_ADDRESS=0.0.0.0`，由管理员配置
Windows 防火墙允许所需内网来源访问 TCP 8000，然后通过 `http://服务器IP:8000` 访问。

## 能耗日/月报：需要补充的配置

在部署目录的 `.env` 中设置以下内容。账号、密码填写当前有效值，不要填旧 AuthId：

```env
ENERGY_API_ENABLED=true
ENERGY_API_BASE_URL=http://10.147.0.38:10091
ENERGY_API_USERNAME=填写能管登录账号
ENERGY_API_PASSWORD='填写能管登录密码'
ENERGY_REPORT_ELECTRICITY_PARAMETER_IDS=2b00e015-d987-f011-a2e9-000c2974f33c,98cbd135-d987-f011-a2e9-000c2974f33c,93fae152-d987-f011-a2e9-000c2974f33c
ENERGY_GAS_PARAMETER_ID=bd3179be-c587-f011-a2e9-000c2974f33c
ENERGY_GAS_UNIT=Nm³
ENERGY_ELECTRICITY_UNIT=
ENERGY_REPORT_MAX_GAP_SECONDS=1800
ENERGY_ALLOW_INTERPOLATION=true
```

账号中的 `@` 直接写，不加反斜杠。电能单位确认是 kWh 后再填 `ENERGY_ELECTRICITY_UNIT=kWh`；
未确认时页面标为“原始单位待确认”。日/月报不需要填写 `ENERGY_EQUIPMENT_IDS`。
程序会自动登录、获取授权和查询采集值，不需要手动复制 token。

修改宿主机 `.env` 后，执行 `docker compose up -d --force-recreate app` 使容器重新读取环境变量；
只执行 `docker compose restart` 不会更新由 `.env` 注入的环境变量。
若刚覆盖了程序文件，则执行前述带 `--build` 的升级命令。
管理页面保存的数据库配置在数据卷 `data/settings.env` 中，仍优先于 `.env` 的对应数据库字段。

管理员进入“设备报表 → 能耗日 / 月报”：

- 日表：按小时查询电耗、气耗，最后一行汇总，并有对应图表。
- 月表：点击“同步本月”查询并保存每天的数据，最后一行汇总，并有对应图表。
- 月度记录保存到原 SQLite 数据卷；不是后台定时自动采集，需主动查询/同步。
- 缺数为 `--`，插值估算为 `≈`；部分数据的合计会明确标注，不伪造为完整实测。
- 当天仅统计已结束小时；服务器及 Docker 容器必须能访问能管内网地址。

## 状态和故障排查

### 文件下载配置

管理员进入“文件派发 → 文件下载”，先添加检索条件并点击“查询文件”，再在下拉框选择文档，点击“下载所选文件到本地”。
条件可增删，支持“全部满足 / 任一满足”；可按船号、工程、文档名、操作日期、版本等查询。
下拉框只显示当前查询页的清单（默认每页10条），不会自动加载全部 PDM 文件。
每次最多50条条件、每个多值条件最多50个值；修改条件后需重新查询，旧结果不可下载。
下载完成后，在“已下载文件”列表选择对应文件；如需拿到操作电脑，点击“另存到电脑”。
展开“检索已下载文件”可复用同一条件编辑器，按下载日期、文件名、版本、状态等筛选本地记录。
“文件派发 → 文件推送”单独管理待派发参数和记录，目前只保存任务，不实际调用机床；两页选择互相独立。

在原 `.env` 中补充：

```env
FILE_DATABASE_ENABLED=true
# FILE_SQLSERVER_* 沿用现场已验证的 PDM 只读连接配置，不是能管账号。
FILE_SOURCE_MODE=http
FILE_HTTP_BASE_URL=http://10.147.0.17:8800
FILE_HTTP_AUTHORIZATION=
FILE_HTTP_COOKIE=
FILE_DOWNLOAD_TIMEOUT_SECONDS=60
FILE_DOWNLOAD_MAX_BYTES=52428800
```

若接口不需要认证，两项认证配置留空；若需要，在部署电脑填入该文件服务自己的认证值。
不要把认证值发到聊天、截图或代码仓库。配置后重新构建启动（仅修改配置则重新创建容器）。
如果管理界面曾保存 PDM 配置，`data/settings.env` 的对应数据库参数优先。
Docker 容器必须能访问 `10.147.0.17:8800`；Apifox 测试成功不能替代容器内网联调。

文件保存在原 `cutting_data` 命名卷内 `/app/data/cutting-downloads/`，不是容器临时磁盘，
重建程序不会清空。文件列表同时保存文档名、版本、大小、SHA-256、下载人和时间。
每次下载有独立目录，不会覆盖旧版本；程序重启后未完成记录标为失败，可以重新下载。
不得删除原数据卷。此功能只下载和选择，不预览内容、不发送到魔盒、不修改 PDM 派发状态。

### 检查容器

```cmd
docker compose ps
docker compose logs --tail 100 app
```

容器应显示 `healthy`。健康检查只说明应用存活，不代表能管账号或 SQL Server 连接已经验证。
能耗显示未启用、认证失败或缺数时，按页面提示检查配置与内网连通性。
不要将含密码的 `.env` 或 `docker compose config` 的完整输出发给其他人。

若内网机器不能联网构建，需要在可联网 Docker 机器上另外制作 Linux AMD64 镜像：

```cmd
docker buildx build --platform linux/amd64 -t cutting-report:local --load .
docker save -o cutting-report-image-amd64.tar cutting-report:local
```

将镜像和本包一起带入内网，配置 `.env` 后运行：

```cmd
docker load -i cutting-report-image-amd64.tar
docker compose up -d --no-build --pull never
```

离线部署不要运行 `START_DOCKER.bat`（它会尝试构建）。更多说明见 `docs/deployment/WINDOWS部署说明.md`。
