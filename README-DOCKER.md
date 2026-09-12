# Windows Docker 部署包 · 2026-09-03

本包沿用之前的 Docker Compose 部署方式，已包含最新的统一设备报表：同一页面显示
“当日任务能耗明细”和“月度每日能耗汇总”，两组内容均为左图右表并带表尾汇总。
文件下载更新版还包括：使用数据库文档名、本地持久化、下载文件下拉选择、已下载列表单选；版本仅作元数据记录，不拼入文件名，也不提供内容预览。
这是源码构建包，不含离线 Docker 镜像；第一次构建需要联网下载依赖。
Windows 上需已安装并启动 Docker，使用 Linux containers。无需单独安装 Python 或 Node.js。

## 本次更新

- 设备报表只统计 `Cutter01`，`Cutter02` 不显示、不汇总；任务明细标题仍为“任务能耗明细”。
- 任务图表、HTML 导出、智能分析及对比数据使用相同设备范围。电耗、气耗自动计算，不需要工人确认；仅车间报表由工人确认任务。误确认可由本人撤销，或由管理员填写原因后撤销、改派，系统保留审计记录。
- PDM 组合检索统一显示“中文业务名称（数据库字段名）”；移除额外项目表的船号、工程名称及计算字段“当前版本物理文件数”，保留原始连表 SQL 中的 `Version` 字段。
- 文件下载使用数据库文档名，版本不拼入文件名；文件推送使用已下载文件的实际文件名作为 `taskName`。
- 新增默认配置 `ENERGY_REPORT_EQUIPMENT_IDS=Cutter01`。旧 `.env` 未填写该项时也使用此默认值。
- 设备范围纳入能耗快照隔离规则：升级后月度表若显示“尚未查询”，点击“同步本月”重新生成当前口径记录。旧快照仍保留在数据卷中，不会被删除。

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
本包不带生产 Excel、历史数据库或密码。任务能耗明细需要从生产数据源读取任务实际
开始、结束时间；月度每日总耗量由能管接口查询并保存在系统数据卷中。

默认本机地址为 `http://127.0.0.1:8088`。首次打开按页面提示创建管理员账号。
如需其他内网电脑访问，在 `.env` 中设置 `DOCKER_BIND_ADDRESS=0.0.0.0`，由管理员配置
Windows 防火墙允许所需内网来源访问 TCP 8088，然后通过 `http://服务器IP:8088` 访问。

## 设备报表能耗：需要补充的配置

在部署目录的 `.env` 中设置以下内容。账号、密码填写当前有效值，不要填旧 AuthId：

```env
ENERGY_API_ENABLED=true
ENERGY_API_BASE_URL=http://10.147.0.38:10091
ENERGY_API_USERNAME=填写能管登录账号
ENERGY_API_PASSWORD='填写能管登录密码'
ENERGY_REPORT_EQUIPMENT_IDS=Cutter01
ENERGY_REPORT_ELECTRICITY_PARAMETER_IDS=2b00e015-d987-f011-a2e9-000c2974f33c,98cbd135-d987-f011-a2e9-000c2974f33c,93fae152-d987-f011-a2e9-000c2974f33c
ENERGY_GAS_PARAMETER_ID=bd3179be-c587-f011-a2e9-000c2974f33c
ENERGY_GAS_UNIT=Nm³
ENERGY_ELECTRICITY_UNIT=kWh
ENERGY_REPORT_MAX_GAP_SECONDS=1800
ENERGY_ALLOW_INTERPOLATION=true
```

账号中的 `@` 直接写，不加反斜杠。电耗使用正常用电单位 `kWh`（1 kWh = 1 度电）。
统一设备报表使用 `ENERGY_REPORT_EQUIPMENT_IDS=Cutter01`，当前只统计 Cutter01；
`Cutter02` 尚未配置仪表，不显示、不汇总，也不能加入同一仪表组。
旧的 `ENERGY_EQUIPMENT_IDS` 不控制这张统一设备报表。
程序会自动登录、获取授权和查询采集值，不需要手动复制 token。

修改宿主机 `.env` 后，执行 `docker compose up -d --force-recreate app` 使容器重新读取环境变量；
只执行 `docker compose restart` 不会更新由 `.env` 注入的环境变量。
若刚覆盖了程序文件，则执行前述带 `--build` 的升级命令。
管理页面保存的数据库配置在数据卷 `data/settings.env` 中，仍优先于 `.env` 的对应数据库字段。

管理员进入“设备报表”：

- 第一张表：只列出所选日期内 Cutter01 的任务，按实际开始、结束时间计算每项任务的电耗和气耗，
  最后一行汇总任务数、时长、电耗和气耗；左侧展示对应任务能耗图。
- 第二张表：记录所选月份内每天的总电耗和总气耗，最后一行汇总整月；左侧展示逐日图。
- 月度记录保存到原 SQLite 数据卷；不是后台定时自动采集，需主动查询/同步。
- 缺数为 `--`，非精确边界按配置规则进行插值估算，数值列不添加额外符号；部分数据的合计会明确标注，不伪造为完整实测。
- 设备页面直接使用当前仪表组，不要求维护 `ENERGY_EQUIPMENT_IDS`；重叠任务不重复分配能耗。
- 服务器及 Docker 容器必须同时能访问生产任务数据库和能管内网地址。

## 状态和故障排查

### 文件下载配置

管理员进入“文件派发 → 文件下载”，先添加检索条件并点击“查询文件”，再在下拉框选择文档，点击“下载所选文件到本地”。
条件可增删，支持“全部满足 / 任一满足”；只提供数据库真实字段，可按 ProjectInfoID、文档名、分组、操作日期、版本、FilePath 等查询。
下拉框只显示当前查询页的清单（默认每页10条），不会自动加载全部 PDM 文件。
每次最多50条条件、每个多值条件最多50个值；修改条件后需重新查询，旧结果不可下载。
下载完成后，在“已下载文件”列表选择对应文件；如需拿到操作电脑，点击“另存到电脑”。
展开“检索已下载文件”可复用同一条件编辑器，按下载日期、文件名、版本、状态等筛选本地记录。
“文件派发 → 文件推送”可保存草稿，也可在二次确认后将准确文件版本上传魔盒并指派到所选机床。

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
FILE_DOWNLOAD_RETENTION_DAYS=7
MAGIC_BOX_ENABLED=true
MAGIC_BOX_TIMEOUT_SECONDS=60
MAGIC_BOX_CONFIG_CACHE_SECONDS=60
```

若接口不需要认证，两项认证配置留空；若需要，在部署电脑填入该文件服务自己的认证值。成功下载的实体文件默认保留7天，服务启动时检查一次，之后每天检查；超期只清理实体文件，下载记录继续保留。若需关闭自动清理，将 `FILE_DOWNLOAD_RETENTION_DAYS` 设为 `0`。
不要把认证值发到聊天、截图或代码仓库。配置后重新构建启动（仅修改配置则重新创建容器）。
如果管理界面曾保存 PDM 配置，`data/settings.env` 的对应数据库参数优先。
Docker 容器必须能访问 `10.147.0.17:8800`；Apifox 测试成功不能替代容器内网联调。
`CUTTER_GATEWAY`、`CUTTER_TOKEN`、`CUTTER_SECRET`、`CUTTER01_IP`、`CUTTER02_IP` 由后端从生产库 `dbo.SYS_CONFIG` 白名单读取，不填入前端也不返回到浏览器。Docker 容器还必须能访问 `CUTTER_GATEWAY`。

文件保存在原 `cutting_data` 命名卷内 `/app/data/cutting-downloads/`，不是容器临时磁盘，
重建程序不会清空。文件列表同时保存文档名、版本、大小、SHA-256、下载人和时间。
每次下载有独立目录，不会覆盖旧版本；程序重启后未完成记录标为失败，可以重新下载。
不得删除原数据卷。系统不预览或执行 GEN；只有“确认并推送”会调用魔盒，上传和指派分阶段记录。本版不修改 PDM 派发状态。

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

## PDF 下载依赖

报表支持 HTML / PDF 两种导出格式。镜像构建会安装 Playwright Chromium、系统依赖和中文字体，
首次构建需要联网；离线服务器请导入新版完整镜像。已有部署执行 `docker compose up -d --build`
后生效，仅重启旧镜像不会增加 PDF 功能。PDF 保存在原报告旁，随 reports 数据卷持久化。
Compose 的 `/tmp` 上限调整为 512 MiB，供 Chromium 临时文件使用；无需额外挂载或开放端口。
