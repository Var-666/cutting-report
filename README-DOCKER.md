# Windows Docker 部署包 · 2026-09-03

进程隔离版本请先阅读 [Web/Worker 升级说明](docs/deployment/WEB_WORKER进程隔离升级说明.md)：首次升级先停止旧单进程容器，app 与 worker 共享原卷并分别运行。以下早期版本说明不替代本次升级与回滚要求。

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
`APP_DATA_SOURCE_MODE` 设置为 `sqlserver`，SQL Server 连接沿用现场配置。
Excel 数据源已移除；旧 Excel 部署须先配置生产库只读连接，再重启新版程序。原始 Excel 资料保留备份，无需挂载输入目录。
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
# 设备范围与仪表绑定只在 backend/app/configuration/devices.json 中维护。
ENERGY_GAS_UNIT=Nm³
ENERGY_ELECTRICITY_UNIT=kWh
ENERGY_REPORT_MAX_GAP_SECONDS=1800
ENERGY_ALLOW_INTERPOLATION=true
```

账号中的 `@` 直接写，不加反斜杠。电耗使用正常用电单位 `kWh`（1 kWh = 1 度电）。
统一设备报表按 `devices.json` 的每台设备范围和仪表绑定统计。Cutter01 已绑定仪表；
`Cutter02` 尚未配置仪表，能耗不显示、不汇总，也不能加入同一仪表组。
旧的 `ENERGY_*_IDS` 等设备环境参数仅做启动一致性核对，不覆盖目录。
程序会自动登录、获取授权和查询采集值，不需要手动复制 token。

修改宿主机 `.env` 后，执行 `docker compose up -d --force-recreate app worker` 使容器重新读取环境变量；
只执行 `docker compose restart` 不会更新由 `.env` 注入的环境变量。
若刚覆盖了程序文件，则执行前述带 `--build` 的升级命令。
管理页面保存的数据库配置在数据卷 `data/settings.env` 中，仍优先于 `.env` 的对应数据库字段。

双设备部署的模型参数建议同步为：

```env
LLM_MAX_CONCURRENT=10
LLM_TIMEOUT_SECONDS=180
LLM_QUEUE_TIMEOUT_SECONDS=120
```

定时报表可使用全部10路模型并发；告警等其他后台分析最多占用一半，前台排队时优先处理前台。
响应时限为180秒，连接超时最多10秒。仅HTTP429自动重试两次：优先遵守Retry-After，否则等待约5秒、10秒并加少量随机延迟。退避不占并发，重试仍受同轮总预算限制，每次尝试分别记录用量，下一轮定时任务独立启动。
已有 `.env` 或 `data/settings.env` 中的旧值会覆盖代码默认值，升级时需核对；
更新配置和代码后按上述方式构建并重建容器。

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
REPORT_RETENTION_DAYS=365
ANALYSIS_RETENTION_DAYS=90
ALARM_RETENTION_DAYS=30
DISPATCH_RETENTION_DAYS=365
AUDIT_RETENTION_DAYS=730
RETENTION_CLEANUP_INTERVAL_HOURS=24
RETENTION_CLEANUP_BATCH_SIZE=500
MAGIC_BOX_ENABLED=true
MAGIC_BOX_TIMEOUT_SECONDS=60
MAGIC_BOX_CONFIG_CACHE_SECONDS=60
```

若接口不需要认证，两项认证配置留空；若需要，在部署电脑填入该文件服务自己的认证值。成功下载的实体文件默认保留7天，服务启动时检查一次，之后每天检查；超期先清理实体文件，相关下载记录再按派发记录保留期清理。若需关闭实体文件自动清理，将 `FILE_DOWNLOAD_RETENTION_DAYS` 设为 `0`。

系统同时执行统一运行数据保留策略：报告文件、生成任务和车间任务快照默认365天、智能分析版本90天、告警按发生时间30天（仅已解除记录）、非活动派发记录365天、确认与管理员权限审计730天。运行中报告、待发送邮件、仍在保留期内的车间任务、活动告警和进行中派发不会删除；报告文件删除失败会保留在SQLite重试队列，下一轮继续清理。对应 `*_RETENTION_DAYS` 设为 `0` 可单独关闭该类清理，修改后需重启服务。
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


## 配置诊断与双进程生效

优先级为 `data/settings.env > 进程环境变量 > .env > 默认值`；设备业务参数只取 `devices.json`。管理页保存到共享 data 卷，不是另一配置来源。HR/PDM 专用身份全部为空时沿用生产连接，专用驱动/加密字段为空时按代码继承生产策略，不能将示例值误当成强制默认。

从管理页保存的自动化/SMTP 在 Web 发布快照，Worker 下一轮操作按文件版本加载；在途操作保留旧配置。数据库、模型、设备目录及其他启动参数需要两个进程重启。修改宿主机 `.env` 必须执行 `docker compose up -d --force-recreate app worker`，只重建 app 或执行 restart 都不能保证两进程一致。源码更新使用 `docker compose up -d --build app worker`。

管理员进入“系统设置 → 配置诊断”，查看每项 Web/Worker 的当前值或安全状态、实际来源、已保存目标及加载时间。`connections.*` 是实际数据库连接及继承来源；账号、密码、密钥、URL 与其他认证信息均脱敏。待重启表示冷配置未加载；配置异常包括目标无效、热快照未更新、两进程值不同或 Worker 心跳不可用。加载批次号不同不直接表示值不同，页面按实际值逐项判定。

容器内只能核对已注入的环境，无法读取宿主机尚未注入的新 `.env`；重建后仍被覆盖时检查 `data/settings.env`，保留数据卷和原配置，不使用 `down -v`。诊断接口只读，不触发热加载、连接测试或任务；异常不影响普通查询与登录。
