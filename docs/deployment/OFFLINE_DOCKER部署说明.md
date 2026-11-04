# 麒麟离线 Docker 部署与更新

离线镜像包含 Python、Microsoft ODBC Driver 18、Playwright Chromium、中文字体和应用依赖。麒麟服务器只在首次部署或依赖变化时导入镜像；普通功能更新只替换程序文件。

2026-10-24 起运行 app/worker 双服务。首次升级先停止旧单进程服务并备份，再按 [进程隔离升级说明](WEB_WORKER进程隔离升级说明.md) 启动；必须同时更新 compose.yaml 和 compose.offline.yaml，不能只替换 backend。后文较早版本记录不改变此升级要求。

## 2026-10-30 uv 安装与镜像校验

当前版本统一通过 uv 0.13.6 及 uv.lock 安装依赖，移除 requirements 导出文件。镜像预检校验 pyproject.toml、uv.lock、.python-version 三份公开文件；旧镜像即使通过自身的旧指纹检查，也会因缺少新指纹而在替换服务前被拒绝。本版必须重新构建和导入配套镜像；下文历史版本的“复用旧镜像”不适用于这次升级。Docker 宿主机不需要安装 uv。

## 首次部署

把镜像包和程序 ZIP 放在同一目录：

```bash
docker load -i cutting-report-image-amd64-YYYYMMDD-vN.tar.gz
unzip cutting-report-kylin-offline-docker-YYYYMMDD-vN.zip
cd cutting-report-kylin-offline-docker-YYYYMMDD-vN
chmod +x scripts/linux/*.sh
./scripts/linux/start_offline_docker.sh
```

镜像只提供稳定运行环境。`compose.offline.yaml` 会将部署目录中的 `backend`、`frontend/dist` 和 `vendor` 只读挂载到容器，因此页面和后端代码以当前程序 ZIP 为准。

## 普通功能更新

普通更新不需要重新导入镜像：

```bash
unzip cutting-report-kylin-offline-docker-新版本.zip
cd 新版本目录
chmod +x scripts/linux/*.sh
./scripts/linux/start_offline_docker.sh
```

启动前核对新包 `.env` 和旧版本的差异，保留现场连接参数并补齐新增配置；不要直接用旧 `.env` 覆盖新包。管理员保存的 `data/settings.env` 仍会覆盖对应配置。

Compose 项目名固定为 `cutting-report`，所以管理员、确认记录、已下载文件和生成报表仍使用原有命名卷。不要执行 `docker compose down -v`。

未涉及数据库结构升级时，可进入旧版本目录再次运行 `./scripts/linux/start_offline_docker.sh` 回滚。

## 多设备版本首次升级

本版新增 Cutter01 / Cutter02 设备隔离，首次启动会升级 SQLite 表结构。升级前自动备份到 data 卷的 `backups/<数据库名>-before-devices-<时间>.db`，历史记录归属 Cutter01。

升级后旧版代码不兼容新的部分唯一约束。如需回退，须先停止容器，保留升级后的数据库，再将升级前备份恢复到原数据库路径，之后启动旧版。恢复备份不会包含升级后新增的确认、告警和报表记录；不要仅切换旧目录启动。

新包使用 `DEVICE_CONFIG_PATH=backend/app/configuration/devices.json`；Cutter02 尚未配置能耗仪表，不会借用 Cutter01 的电表和气表。告警保留期以 `.env` 的 `ALARM_RETENTION_DAYS` 为准，清理仅针对已关闭且超期的告警。

## 需要更新镜像的情况

以下文件或运行环境发生变化时，需要核对是否更新离线镜像：

- `backend/pyproject.toml`、`backend/uv.lock`、`backend/.python-version`；
- `Dockerfile` 中的 Python、ODBC、Chromium或系统包；
- 容器启动方式或基础镜像。

2026-10-13 版本仅移除 Excel 数据源、`openpyxl` 依赖及输入目录挂载，没有新增依赖或变更现有依赖版本要求，可继续使用已有镜像。新包 `.env` 已使用 SQL Server，模型并发为 4、响应时限 90 秒、排队时限 120 秒；不要用旧 `.env` 覆盖这些更新。若现场 `data/settings.env` 仍配置 Excel 模式，须先在旧版数据库配置页切换为 SQL Server 并保存完整连接，之后再升级。

## 2026-10-19 更新配置

本版依赖和 Docker 配置与 2026-10-13 包一致，继续使用已有镜像。部署 `.env` 已将 `REPORT_BACKGROUND_REFRESH_SECONDS` 从 600 改为 300；若现场 `data/settings.env` 也保存了该项，须同步为 300 后重新运行启动脚本。

本轮部署配置为`LLM_MAX_CONCURRENT=10`、`LLM_TIMEOUT_SECONDS=180`、`LLM_QUEUE_TIMEOUT_SECONDS=120`；旧程序代码最多接受8路，因此先更新程序包，再使用10路配置并重新运行启动脚本。当前离线部署挂载后端代码，无须重新制作基础镜像；检查`data/settings.env`中的同名旧值覆盖。

两台设备的领导、车间、设备报表每轮强制重新分析，数据不变也调用模型；上一轮未完成时新轮次独立运行，受全局10路容量及超时限制。固定周期任务可使用全部容量；普通页面查询继续使用缓存。仅HTTP429最多重试两次，遵守Retry-After或5/10秒加少量随机延迟退避；等待不占并发，重试不重置同轮预算，每次HTTP尝试单独计数。此策略替代旧版后台复用规则，会增加模型调用量。生产数据刷新不等待模型结果，分析更新或失败时页面保留同设备、同区间的上次结果并标记状态。

## 2026-10-21 更新注意事项

相较 2026-10-19 部署包，`.env` 的模型并发由 4 改为 10，响应时限由 90 改为 180 秒；排队时限仍为 120 秒，后台周期仍为 300 秒。依赖、Docker 配置和启动脚本不变，继续使用已有镜像；应同时更新程序与配置，再运行启动脚本重建容器，旧代码不接受 10 路配置。

三类默认报表页面改为只读最近成功分析，每 10 秒检查新结果；打开页面不再自动发起模型分析。后台仍每 300 秒独立生成，手动重新分析、自选区间及导出保留原流程。升级时自动为 SQLite 分析表新增可空 `display_scope` 字段及索引，保留旧缓存，但不推测旧缓存的默认/自选范围；新展示接口需等待升级后首轮默认分析成功，尚无结果时显示等待，后续刷新失败保留上次成功结果。

升级后核对 `data/settings.env` 是否有同名旧值覆盖。包内邮件与 SQL 工作台仍因必要参数缺失保持关闭；独立文件库连接为空时沿用现有生产库回退规则或现场已保存配置，不能仅凭启用开关判断连接可用。

## 2026-10-22 更新注意事项

相较 2026-10-21 包，环境变量、依赖、Docker 配置及启动脚本不变，继续使用已有镜像。文件派发页的目录、历史与状态检查独立读取；文件名的两位材质代码原样传递，不再转换为牌号。首次启动自动创建文件派发状态摘要表，并从现存历史回填；之后历史清理不删除该摘要，升级前已清理的历史无法补回。文件列表显示最新派发状态并在派发后刷新，目录全选支持跨页，仍受 100 项上限约束。

## 2026-10-24 双进程升级

本版改为同一镜像运行 Web 与 Worker 两个服务；Python 运行依赖不变，前端已在程序包中构建，离线部署可复用现有 `cutting-report:local`。首次升级须先在旧目录执行 `docker compose stop` 并备份配置及 data/reports 卷，核实历史设备归属，再解压新包运行原启动脚本。完成后检查 `app`、`worker` 均 healthy，日志使用 `docker compose logs --tail 100 app worker`；不要让旧版后台与新版 Worker 混跑。

迁移与回滚请按 [Web/Worker 升级说明](WEB_WORKER进程隔离升级说明.md) 和 [多设备隔离迁移说明](../project/multi-device-isolation.md) 操作。已有空日报若没有明确设备归属，迁移会回滚并拒绝启动；不得凭默认设备赋值或删除记录绕过。`.env` 仍为原 134 项，设备业务参数以 `devices.json` 为准，旧环境变量与目录冲突也会拒绝启动。请在新版“配置诊断”核对两个进程的实际生效值。

### 20261024-v2

相较 v1，数据库页全部配置保存后按新请求/任务热加载，Web 和 Worker 各自更新运行快照，在途操作使用原连接；模型、能管和设备目录等启动参数仍须重建两个服务。前端移除重复设置首页，原设置地址跳转数据库配置，并更新登录、首页、派发目录和异常配置诊断布局。依赖、镜像、Compose 与 `.env` 值均未变化。

v2 没有跳过设备归属迁移，也不会自动删除历史日报。若 v1 因“日报设备归属不明确”退出，须先完成已核准的历史数据处理，再启动 v2；仅替换程序包不能清除此阻塞。处理前应备份原数据库，勿清空数据卷。

### 20261024-v3

相较 v2，仅更新首页指标卡、近期趋势柱图、明暗主题层次及派发项目搜索交互。后端、迁移逻辑、依赖、Compose、启动脚本和 `.env` 值均不变，可沿用现有镜像；历史设备归属问题仍须按前述说明处理。

## 2026-10-26 焊接版本

本版新增小组立焊接线、按产线类型导航、焊接报表/导出及连接配置。Python 新增 `mysql-connector-python>=9.2,<10`；旧镜像没有此驱动，启用焊接前需更新镜像。配套 AMD64 离线镜像是在原镜像上补装 MySQL Connector/Python 9.7.0 并加入核验脚本，保留原 Python、ODBC、Chromium 和字体。程序 ZIP 与镜像 TAR.GZ 分别提供。

先在旧部署目录停止 app/worker 并备份配置及数据，再将两个交付文件放在同一目录执行：

```bash
docker load -i cutting-report-image-amd64-20261026-v1.tar.gz
docker tag cutting-report:runtime-20261026-v1 cutting-report:local
unzip cutting-report-kylin-offline-docker-20261026-v1.zip
cd cutting-report-kylin-offline-docker-20261026-v1
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

`.env` 补齐为 144 项，新增 10 项 `WELD_*`；未提供真实焊接连接，`WELD_ENABLED=false`。部署后管理员可在数据库配置页测试并保存只读 MySQL 连接，再启用；未启用不查询焊接源库，不影响切割功能。端口 3306、库名 `weld_production_data` 仅沿用初始配置，须现场核实。模型仍为并发10、响应180秒、排队120秒；后台周期300秒、告警保留30天。SMTP/SQL 工作台缺参数保持关闭，文件库沿用现场配置或既有回退规则。

此包没有取消历史日报设备归属校验，也不会自动删除服务器记录。首次升级若仍存在已报告的无归属日报，应先完成获准的数据处理；换镜像不能解决该数据迁移阻塞。焊接真实连接、字段口径及性能仍需按 [焊接接入说明](../project/welding-integration.md) 在现场验收。

### 20261026-v2

相较 v1，修复仅保存焊接连接时被未配置的切割库阻止、查询超时后连接池无法恢复的问题，并减少焊接统计中的重复计算和关联扫描。数据库配置页补充焊接必填校验及未保存状态提示。依赖、Docker 配置和启动脚本不变，可复用 `cutting-report-image-amd64-20261026-v1.tar.gz` 对应镜像，无须重新导入；此前未导入该镜像的服务器仍须先按上面的命令导入并设置标签。

部署 `.env` 的 `WELD_DB_QUERY_TIMEOUT_SECONDS` 从 30 调整为 120，每条 SQL 独立计时，仍服从调用方剩余时限；连接超时保持 5 秒。如果现场 `data/settings.env` 保存了同名旧值，也须同步调整，然后运行启动脚本重建 app/worker。焊接、邮件和 SQL 工作台仍因必要参数缺失保持关闭，其余业务启用状态不变。本包不会自动删除历史日报或绕过设备归属迁移。

### 20261026-v3

本包对应提交 `351663b`，修复焊接查询容量立即拒绝和导出占用查询位的问题。默认4个执行位、最多12个FIFO异步等待、排队上限5秒；现场显式使用较小连接池时执行数同步降低。取消或超时的等待请求不再迟到执行，正在运行的同步查询结束后才释放容量。PDF查询准备完成后转入现有PDF资源限制，AI读取数据也经过焊接查询池；连接故障、连接池耗尽、SQL错误和超时分别诊断。作业列表复用同一事务的汇总总数；前端复用相同在途请求，并移除503自动重试循环。

相比 v2，部署 `.env` 仍为144项，仅 `WELD_DB_POOL_SIZE` 从2改为4；单SQL超时保持120秒，连接超时保持5秒。若服务器 `data/settings.env` 保存了旧池大小或旧超时，须同步核对后重建两个容器。焊接连接、SMTP和SQL工作台仍因必要参数缺失保持关闭；已在服务器网页保存的焊接连接和启用状态继续由原数据卷中的配置覆盖。

依赖、镜像要求、Compose和启动脚本均无变化。已导入 `cutting-report-image-amd64-20261026-v1.tar.gz` 并标记为 `cutting-report:local` 的服务器可直接更新程序包，无须重新导入镜像。更新前保留现场连接配置并备份数据，随后执行：

```bash
unzip cutting-report-kylin-offline-docker-20261026-v3.zip
cd cutting-report-kylin-offline-docker-20261026-v3
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

本次修复已通过66项后端、17项前端定向用例。未连接生产MySQL，现场慢查询、网络及多用户表现仍需验收；没有自动删除历史日报或跳过设备归属迁移。

## 20261027-v1

本包包含 `7676958` 及当前工作区旧浏览器查询参数修复。前端完成严格TypeScript迁移、API按业务拆分和报表表格样式调整；构建入口新增类型检查及前端测试。产线目录、服务选择和功能范围统一注册。焊接请求日期必填，统计按规范化筛选及源更新时间最多复用30秒，分页仍核对源信息并读取当页明细；导出绕过缓存，保留同一事务完整计算。未推进源表最大更新时间的变更可能使页面汇总在30秒内短暂落后于明细，现场须核对实际更新规则。

旧浏览器缺少 `URLSearchParams.size` 时，现在仍保留设备、日期、筛选及导出格式参数。部署后HTML会重新验证，带内容哈希的JS/CSS使用长期缓存；页面若一直未关闭，建议重新打开或刷新。

相比20261026-v3，部署 `.env` 仍为144项，全部值不变。能管、HR认证、文件库、魔盒、智能分析、告警和自动化开关开启；焊接连接、SMTP及SQL工作台仍缺少必要参数而关闭。现场已在网页保存的焊接配置仍通过原data卷的 `settings.env` 生效，不能用包内空连接覆盖。端口8088、告警保留30天、焊接连接池4、单SQL超时120秒、模型并发10保持不变。

后端依赖、Dockerfile、Compose、设备迁移及启动脚本未变。新增TypeScript等开发依赖仅用于打包时构建前端，服务器不需要安装Node或下载这些依赖；已导入20261026-v1配套镜像的服务器无须更新镜像。按既有流程备份配置和数据，然后执行：

```bash
unzip cutting-report-kylin-offline-docker-20261027-v1.zip
cd cutting-report-kylin-offline-docker-20261027-v1
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

服务器若仍使用20261026以前、不含MySQL驱动的镜像，先导入20261026-v1配套镜像并按前述命令设置 `cutting-report:local` 标签。本包不清理历史日报、不绕过设备归属迁移，真实数据库和浏览器验收仍须在现场完成。

发布验证：86项后端定向用例通过，前端类型检查通过；前端306项中305项首次通过，数据库连接测试夹具补齐真实产线目录与 `selectedLineType` 注入后，失败的1项单独重跑通过。其余已通过测试未重复执行；未连接生产数据库。

## 20261027-v2

本包包含提交 `16d223c` 及此前的多产线报表基础设施更新。切割领导月报保留原有历史快照与业务接口；焊接统计接入共享L1及SQLite持久化快照，普通统计命中无需访问MySQL，作业/工件实时读取明细和分页总数，导出仍重新计算。快照按源身份、筛选与版本隔离；过期时明确展示截止时间/来源/状态，并复用现有Worker队列刷新。页面请求竞争、路由筛选与分页恢复、焊接图表实例复用、工件状态及旧浏览器取消/导出兼容已同步修复。

相比20261027-v1，部署 `.env` 仍为144项，配置值不变：端口8088、告警保留30天、后台刷新300秒、焊接池4、SQL查询超时120秒、模型并发10。能管、HR认证、文件库、魔盒、智能分析、告警及自动化已开启；焊接、SMTP、SQL工作台因本地缺少必要连接或认证参数仍关闭。原data卷中网页保存的焊接连接和启用状态继续生效；`data/settings.env` 覆盖包内同名配置。

Python依赖、Dockerfile、Compose与启动脚本未变，复用20261026-v1配套镜像，无须重新下载依赖或安装前端工具。首次启动只在应用SQLite增量创建 `report_snapshots`，不修改生产数据库或重建旧业务表。升级前备份配置及SQLite；本版Web/Worker须一起更新。回退到上版前须停止两个服务，并按[多产线报表基础设施](../project/multi-line-report-data.md#sqlite变化与回滚)处理未完成的 `report_snapshot_refresh` 任务，不能仅切回旧目录启动Worker。该说明已加入本包。

```bash
unzip cutting-report-kylin-offline-docker-20261027-v2.zip
cd cutting-report-kylin-offline-docker-20261027-v2
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

未执行真实MySQL/SQL Server连接、压测或生产部署；现场仍需核对数据截止时间、统计与实时明细差异及刷新故障表现。历史设备归属迁移校验继续保留，不自动删除历史日报。

## 20261028-v1

本包包含 `1088075`，焊接首页及四类报表统一为完整页面快照优先。快照包含同一源事务生成的汇总、图表、当页明细和总数，按日期、筛选、排序及分页隔离；首页独立生成，不再等待工件报表完成。Web普通页面读取不查询MySQL，首次缺失时提交原Worker队列并显示后台准备状态；只针对该状态有界等待重读，不重放连接、权限或容量错误。已有快照时先展示原数据，后台完成后自动替换，明确标记截止时间、来源、过期及刷新状态。工件下钻与导出仍读取源库，导出保持全部筛选结果和既有限额。登录预加载与页面共用产线内请求缓存，退出/切换仍清理。

相比20261027-v2，部署 `.env` 仍为144项，值全部不变。原有能管、HR认证、文件库、魔盒、智能分析、告警与自动化开关开启；本地未提供焊接主机/账号/密码、SMTP必要参数及SQL工作台Token，这三项仍关闭。服务器data卷中网页保存的焊接连接及启用状态继续优先生效。端口8088、后台刷新300秒、告警保留30天、焊接池4、查询超时120秒、模型并发10保持不变。

Python/前端依赖、Dockerfile、Compose及启动脚本没有变化，可复用20261026-v1配套镜像；无需重新导入或下载依赖。本次没有新增SQLite表结构或更改设备迁移。新页面快照键与旧聚合快照共存，首次访问需等待Worker生成相应页面；不能仅启动Web。若返回准备状态长期不结束，应检查Worker状态、数据库连接及两个服务日志，不应删除data卷。

按原流程备份配置与数据，再一起更新Web/Worker：

```bash
unzip cutting-report-kylin-offline-docker-20261028-v1.zip
cd cutting-report-kylin-offline-docker-20261028-v1
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

原 `report_snapshots` 表和升级前内部聚合任务在新版继续兼容；回退20261027-v2或更早版本前按[多产线报表基础设施](../project/multi-line-report-data.md#sqlite变化与回滚)清理不兼容的待执行刷新任务。本包不自动删除历史日报、不跳过设备归属校验。未做生产数据库连接或部署验收，首次生成耗时及现场刷新效果仍须验证。

## 20261028-v2

本包包含当前工作区尚未提交的焊接SQL优化及Worker升级队列修复，基于 `1088075`。焊接公共统计使用共享区间CTE，减少重复扫描；工件先匹配参与流水号，项目统计与汇总合并，保持原筛选、分页、归属及单位。源表更新时间/设备目录按后台刷新间隔采样，并显示独立采样时间，不能把它当作本次业务统计的计算时间。Worker消费旧版聚合任务时跳过已被页面快照替代的查询，避免旧任务继续占用生成通道；保留其他任务和租约保护，日志补充类型、区间、结果和耗时。

相较20261028-v1，部署 `.env` 仍为144项，值全部不变。能管、HR认证、文件库、魔盒、智能分析、告警与自动化已开启；焊接、邮件和SQL工作台仍因本地缺少连接或认证参数保持关闭。现场原data卷中的网页配置继续覆盖同名环境值。端口8088、后台刷新300秒、告警保留30天、焊接池4、SQL超时120秒、模型并发10不变。

依赖、Dockerfile、Compose、启动脚本及SQLite结构无变化，继续使用20261026-v1配套镜像，无需重新导入镜像或下载依赖。本次查询使用CTE语法，现场MySQL须支持该语法；已有隔离MySQL验证记录不等同于现场兼容性与性能验收。包内不包含生产数据，不执行源库建表、修改索引或写入操作。

备份配置和应用数据后，将Web/Worker一起更新：

```bash
unzip cutting-report-kylin-offline-docker-20261028-v2.zip
cd cutting-report-kylin-offline-docker-20261028-v2
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

无需手动清空刷新队列或删除data卷。首次页面快照仍由Worker生成；用新日志核查任务是否完成、源查询耗时及失败退避。没有取消设备归属迁移校验，历史日报不会自动删除。回退到旧版本仍按[多产线报表基础设施](../project/multi-line-report-data.md#sqlite变化与回滚)处理不兼容的未完成刷新任务。真实源库性能和数据勾稽需现场验证，不以本地合成数据的优化比值作部署承诺。

## 20261029-v1

本包基于 `f0236a1`，包含当前工作区工程编号与HTML缓存修复，以及此前已打包的焊接SQL/旧刷新任务修复。前端表格、表单、弹层及目录等控件统一，修复日期选择草稿丢失、车间翻页回顶并保留同路径查询参数切换时的滚动位置。HTML根入口与深链接响应改为 `Cache-Control: no-store`，哈希JS/CSS继续长期缓存；升级后浏览器强制刷新一次，核对加载的是新页面。

切割工程号、船号及选项/筛选/领导分布统一使用已有名称，名称为空时回退 `PROJECT_CODE`。已有车间日报仅在其他任务字段一致时补齐空编号，保留确认及审计；其他差异仍拒绝静默覆盖。领导月报快照映射版本升级，旧映射数据按原流程重采，不删除历史业务记录，不写入生产SQL Server。

相较20261028-v2，部署 `.env` 仍为144项，全部值不变。能管、HR认证、文件库、魔盒、智能分析、告警及自动化已开启；本地焊接连接、SMTP必要参数及SQL工作台Token仍为空，这三项保持关闭。原data卷中网页保存的连接和启用状态继续通过 `settings.env` 覆盖同名环境值。端口8088、后台刷新300秒、告警保留30天、焊接池4及单SQL超时120秒、模型并发10不变。

Python依赖、前端运行/开发依赖及锁文件、Dockerfile、Compose和启动脚本均未变，前端package.json仅扩充发布测试入口；复用20261026-v1配套镜像，无需重新导入镜像或下载依赖。无新增SQLite表结构或设备迁移。保留此前焊接CTE查询及Worker页面快照机制，真实源库兼容性、重采耗时和页面效果需在现场核对。

备份现场配置及应用数据后，一起更新Web/Worker：

```bash
unzip cutting-report-kylin-offline-docker-20261029-v1.zip
cd cutting-report-kylin-offline-docker-20261029-v1
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

不要执行 `docker compose down -v`。首次/旧版本领导快照可能需要重算；焊接页面首次快照仍由Worker生成。回退到不支持新快照任务的旧版前按[多产线报表基础设施](../project/multi-line-report-data.md#sqlite变化与回滚)处理未完成任务。本包不自动删除历史日报、不跳过设备归属校验，未执行生产数据库连接或真实部署。

## 20261029-v2

本包包含当前工作区尚未提交的日期、派发及告警交互修复，沿用 `f0236a1` 和此前已交付的工程编号/焊接优化。三张标准报表应用日期后等待筛选重置并查询一次；日期日历挂到body避免容器裁切。派发目录文件每页20条自然展开，切换目录失败不会残留旧文件，翻页只使用已应用的搜索条件并可局部重试。告警读取失败明确显示未知状态或保留上次结果，避免误报正常/空态和页码错位。

派发记录新增船号/分段号持久化，记录表拆成11个独立单行字段，任务标识和魔盒UUID相邻；旧记录分段可按已有文件名回退展示。首次启动在应用SQLite的 `cutting_dispatches` 表增量添加可空 `ship_no` 和 `segment_name`，不会删除旧记录或修改生产PDM/魔盒数据。升级前备份SQLite及配置；两个服务一起更新。原任务执行、重试、未知结果保护和权限继续保留。

相较20261029-v1，部署 `.env` 仍为144项，配置值全部不变。能管、HR认证、文件库、魔盒、智能分析、告警和自动化已开启；本地焊接、SMTP及SQL工作台必要参数仍不完整，对应开关保持关闭。现场 `data/settings.env` 继续覆盖同名值，已保存的焊接连接和启用状态沿用原数据卷。端口8088、告警30天、后台300秒、焊接池4/SQL超时120秒及模型并发10不变。

运行依赖、前端依赖锁文件、Dockerfile、Compose与启动脚本没有变化；前端发布脚本增加交互状态测试，服务器无需安装Node或下载依赖，继续复用20261026-v1配套镜像。

```bash
unzip cutting-report-kylin-offline-docker-20261029-v2.zip
cd cutting-report-kylin-offline-docker-20261029-v2
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

升级后浏览器强制刷新一次，并检查Web/Worker均正常。新字段允许空值，旧记录没有的船号不凭空补造；页面展示和外部派发仍需现场验收。保留已有设备归属校验和新快照任务回滚要求，不能删除data卷解决升级问题。未执行生产数据库连接、真实PDM/魔盒派发或服务器部署。

发布验证：129项后端定向用例通过，包含旧派发记录增量加列、字段保存、重试和未知结果保护；前端类型检查通过。424项前端发布测试中423项首次通过，1项告警布局断言按新分页变量和失败态补齐后单独重跑通过，其余通过项未重复执行。

## 20261030-v1

本包基于32014e9，承接上版日期/派发/告警修复；本次主要统一车间、文件派发和告警API响应契约及生成的前端类型，将SQLite结构升级归并为13组编号迁移，拆分用户/会话/日报/确认仓储并保留原调用门面。焊接SQL优化及旧快照队列处理继续保留。新迁移工具和API契约生成脚本已补入部署包。升级会为已有SQLite创建一致性备份，结构与迁移标记在事务内更新；仍须先备份现场配置及应用数据，不删除历史日报、不绕过设备归属校验。

**本次必须更换配套镜像。** 后端改用uv 0.13.6与uv.lock安装，镜像入口及启动预检验证pyproject.toml/uv.lock/.python-version三个输入的指纹；20261026-v1及更早镜像会被拒绝。配套镜像为AMD64、Python3.12.14，包含ODBC Driver 18与Chromium。前端构建切换pnpm10.34.9，开发构建需要Node>=22.18；服务器离线部署无需安装Node、Python或联网拉取依赖。Compose项目名、数据卷及8088端口不变。

包内.env仍为144项，与20261029-v2逐值一致，未遗漏当前示例或代码声明字段。能管、HR认证、文件库、魔盒、LLM、告警监控及自动化已开启；焊接因主机/账号/密码为空、SMTP因主机/发件人等必要参数缺失、SQL工作台因管理员Token为空，仍关闭。服务器原data/settings.env继续覆盖同名值，网页已保存的焊接连接和启用状态沿用原数据卷。告警保留30天、后台刷新300秒、焊接池4/SQL超时120秒及模型并发10保持不变。

将程序ZIP及配套镜像放在同一上传目录。先停止旧目录中的Web/Worker并备份配置和SQLite，然后执行：

```bash
# 在旧部署目录执行，保留原有数据卷
# docker compose stop

# 回到上传目录；本次新镜像只需导入一次
sha256sum -c cutting-report-image-amd64-20261030-v1.tar.gz.sha256
docker load -i cutting-report-image-amd64-20261030-v1.tar.gz
docker tag cutting-report:runtime-20261030-v1 cutting-report:local
unzip cutting-report-kylin-offline-docker-20261030-v1.zip
cd cutting-report-kylin-offline-docker-20261030-v1
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

不要执行down -v。依赖指纹不匹配时预检在重建服务前退出，应核对新镜像标签及本包锁文件，不能跳过检查。不能只更新Web或仅替换启动命令。后续锁文件不变时可继续复用本次镜像；锁输入再次变化则需匹配镜像。升级后浏览器强制刷新一次；真实数据库、生产规模、能管和PDM/魔盒仍需现场验收。本包含部署凭据，仅用于指定公司服务器，不纳入Git或公开分发。

发布验证：88项后端定向检查最终通过，其中2项原生安装用例首次因本机Python3.14不受支持而失败，切换已有Python3.12环境后仅重跑这2项并通过；其余通过项未重复执行。API类型生成一致性检查、前端类型检查及424项前端发布测试通过。AMD64镜像禁网只读运行验证Web/Worker健康、13组迁移及外键完整性；ODBC Driver 18和Chromium中文PDF验证通过。未连接生产数据库或部署到现场服务器。

## 20261102-v1

本包基于769aafc，包含当前尚未提交的PDM文件类型契约修复：文件列表及条件检索接受字符串、整数和空值，保留来源原值，避免数字类型触发响应校验失败；前端生成类型已同步。焊接首页和四类报表统一公共视觉组件、主题和分析展示，分页明细及关联焊缝固定10条/页、自然展开，保留筛选/导出/URL状态。派发、车间确认、自动化、设计器、区间能耗和领导报表拆分为组件及composable，沿用原业务接口与权限；没有新增生产数据库写入、后台任务或SQLite结构迁移。预览夹具不作为生产入口，也不收录在本包。

相较20261030-v1，后端/前端依赖及锁文件、Dockerfile、Compose、入口和启动脚本均未变化。继续复用cutting-report:runtime-20261030-v1配套AMD64镜像，不需要再次导入或联网下载依赖；服务器若还停留在20261026镜像，应先按上节导入20261030镜像，不能跳过依赖指纹检查。服务器已迁移到/data/docker时，仍沿用原项目目录和启动步骤，包内不修改Docker存储路径。

部署.env仍为144项，与20261030-v1逐值一致，覆盖当前示例及代码声明。能管、HR认证、文件库、魔盒、LLM、告警和自动化开关开启；焊接主机/账号/密码、SMTP主机/发件人和SQL工作台管理员Token仍未完整配置，这三项保持关闭，不能仅打开开关代替连接参数。现场data/settings.env和原数据卷继续保留，网页已保存的连接和启用状态优先。端口8088、告警30天、后台刷新300秒、焊接池4/SQL超时120秒及模型并发10不变。

升级前备份.env与应用数据，并在旧部署目录停止本项目两个服务；然后在上传目录执行：

```bash
unzip cutting-report-kylin-offline-docker-20261102-v1.zip
cd cutting-report-kylin-offline-docker-20261102-v1
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

Web/Worker一起更新，沿用cutting-report项目及原有数据卷；不要执行down -v，不删除历史日报、不跳过归属校验。更新后浏览器强制刷新一次，现场验收焊接分页/分析及PDM实际文件列表。本包含部署凭据，仅供指定公司服务器使用，不纳入Git或公开分发。真实生产连接和页面业务验收未在本次打包时执行。

发布验证：29项后端契约/打包定向检查通过；API生成类型一致性、前端类型检查和完整发布构建通过。已在禁网AMD64镜像中验证当前三个锁输入的指纹与20261030-v1镜像一致。本次不重复执行未变化的SQLite迁移、Chromium或外部连接测试；保留已有非阻塞构建大包提示。

## 20261102-v2

本包基于769aafc及当前未提交的响应契约修复，承接20261102-v1的焊接UI统一和页面职责拆分。新增兼容历史告警措施字符串优先级、历史任务确认空report_id，派发is_simulation响应使用布尔类型；保留v1的PDM数字文件类型支持。生成告警分析时按完整模型验证字段，并约束置信度在0–1，避免格式不完整的模型结果写入后令响应失败。前端生成类型同步；不改历史数据或生产数据库、不新增SQLite迁移。

依赖及锁文件、Dockerfile、Compose、启动脚本与20261102-v1一致，复用20261030-v1配套AMD64镜像，无需重新导入；20261026及更早镜像仍不能通过依赖指纹校验。部署.env仍为144项、与v1逐值一致，覆盖示例及代码声明。能管、HR认证、文件库、魔盒、LLM、告警和自动化已开启；焊接主机/账号/密码、SMTP必要参数和SQL工作台管理员Token仍缺失，这三项保持关闭。服务器data/settings.env继续覆盖同名值，不修改现有Docker存储位置和数据卷。端口8088、告警30天、后台300秒、焊接池4/SQL120秒、模型并发10不变。

前端仅生成类型声明变化；已确认Vue/业务TS、配置、锁文件及dist与今日v1相同，沿用v1已完成437项发布测试及构建的产物。本次补做类型一致性、TypeScript和受影响前端用例，不重复全量测试/构建；后端按接口、生成校验和打包范围验证。保留已有非阻塞构建大包提示。

升级前备份.env及应用数据，在旧部署目录docker compose stop后，回到上传目录执行：

```bash
unzip cutting-report-kylin-offline-docker-20261102-v2.zip
cd cutting-report-kylin-offline-docker-20261102-v2
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

Web/Worker一起更新，不执行down -v，不删除历史日报或绕过设备归属校验。本包含部署凭据，仅供指定公司服务器使用。未连接生产数据库、真实模型或PDM/魔盒，现场真实历史值和新生成告警仍需验收。

发布验证：50项后端定向（46项接口/真实SQLite服务路径/打包及4项告警分析）、8项前端定向、API生成类型一致性及前端类型检查全部通过。前端产物复用前已逐文件核对与v1一致；未重复执行未变更的迁移、PDF和镜像验证。

## 20261103-v1

本包基于f2b712c，包含本轮后端Runtime/配置保存及模块边界整理、API契约补全、焊接聚合快照复用和通用数据源管理。日报与设备页共用规范化聚合快照，分页/排序只投影原快照，保留其截止时间/过期与刷新状态；任务/工件页继续独立分页快照，导出/下钻仍实时读取。首页与报表设计统一，缺失工程号、船名、分段名在相关任务表中按字段显示“非船”，保留原始数据和任务归类。

新增可选BM船名关联：在“数据库配置 → 其他数据源”登记ID为bm的SQL Server连接并启用，需读取dbo.BM_PROJECT的ProjectNum/ProjectName。完整工程号精确映射船名，不改变工程统计键；未登记/停用不影响启动，查无匹配或冲突时保留任务，船名为空。网页保存后Web/Worker按新操作热加载，不需要新建产线。现场连接信息未提供，本包不猜填BM账号或启用虚构数据源。

首次启动执行014_workshop_ship_names，在应用SQLite的workshop_report_items增加独立ship_no列，保留旧project_name及确认/审计/修订；既有日报兼容船名补齐。沿用编号迁移自动备份/事务/外键校验，不修改生产SQL Server/MySQL结构，不清空历史日报。升级前务必备份配置及应用SQLite，Web与Worker一起更新。不要执行down -v或仅替换其中一个进程。

相较20261102-v2，Python/前端依赖锁、Dockerfile、Compose和启动脚本不变，继续复用20261030-v1配套AMD64镜像，无需重新导入。package.json只增加已有依赖下的新数据源前端测试入口；服务器无需安装Node/Python或联网下载依赖。旧20261026镜像仍会被预检拒绝，应先按20261030-v1说明导入配套镜像。

部署.env补齐新增DATABASE_SOURCES_JSON=[]，共145项；其余144项与上包逐值相同。空列表仅表示包内尚未登记额外连接，现场data/settings.env优先，已保存的数据源不会被本包空列表覆盖。能管、HR认证、文件库、魔盒、LLM、告警及自动化开关已开启；焊接主机/账号/密码、SMTP必要参数及SQL工作台Token仍缺失，这三项保持关闭。端口8088、告警保留30天、后台300秒、焊接池4/查询120秒及模型并发10不变。是否实际接通仍须看现场配置与只读业务验收。

先在旧部署目录停止本项目两个服务并备份，再回上传目录执行：

```bash
unzip cutting-report-kylin-offline-docker-20261103-v1.zip
cd cutting-report-kylin-offline-docker-20261103-v1
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

沿用cutting-report项目和原数据卷，Docker已经迁移/data/docker时继续原路径，无需修改本项目目录或.env。更新后强制刷新浏览器；新聚合快照按原Worker生成，页面准备状态需观察Worker日志。回退到旧版应停两个服务并按多产线报表说明处理不兼容待执行快照任务；新船名列不删除，如需完整回退数据仅恢复停机备份，避免覆盖升级后新确认或派发记录。本包含部署凭据，仅用于指定公司服务器，不公开分发。

发布验证：267项后端定向最终通过（221项接口/配置/数据源/BM/快照/迁移/打包中2项仅因模拟焊接客户端缺少close接口失败，补齐测试夹具后仅重跑这2项通过；另46项Core/报表生成/分析/自动化/Worker集成通过）。API类型一致性、前端类型检查及426项前端发布测试/构建通过。现有AMD64镜像依赖指纹验证、禁网只读临时容器Web/Worker健康、14组迁移及ship_no列/外键检查通过。未连接生产库、真实BM、模型、能管或PDM/魔盒；未运行后端全量或生产压测，既有构建大包提示保留。

## 20261103-v2

本包基于f2b712c及当前未提交的旧SQL Server TLS兼容和区间报表修复，承接v1的BM船名关联、通用数据源与014船名迁移。区间车间报表现在注入独立ConfirmationRepository读取设备确认，修复原仓储接口不匹配；BM不可用时仍保留任务、设备确认隔离及原统计。

“数据库配置 → 其他数据源”的SQL Server编辑框新增“兼容旧版SQL Server TLS”。仅遇到legacy sigalg disallowed or unsupported且旧服务器不能更新时，在对应数据源中显式勾选，测试连接后保存。legacy_tls默认false，不属于全局.env开关；启用后仅该数据源的批量只读查询和SELECT 1连接测试使用一次性子进程及专用OpenSSL配置，允许TLS1.0和弱签名。父Web/Worker、宿主机及其他连接的TLS策略不变；取消/期限/连接和查询超时会回收子进程，凭据不写命令行、临时文件或日志。此模式不支持直接连接或流式查询；数据库端更新TLS/证书后关闭兼容。

后端需包含sqlserver_legacy.py和sqlserver_legacy.cnf并同步更新前端，Web/Worker一起重建。本次无新增依赖、Docker配置或SQLite迁移，复用20261030-v1配套AMD64镜像，无需重新导入镜像或联网安装依赖。仍保留v1的14组迁移；从v1升级不会重复已完成的迁移，从更早版本升级应先备份SQLite及配置。

部署.env共145项，与20261103-v1逐值一致，代码及示例声明无遗漏。能管、HR认证、文件库、魔盒、LLM、告警和自动化开启；焊接、SMTP及SQL工作台缺少必要参数，仍关闭。DATABASE_SOURCES_JSON=[]仅为空默认登记列表；现场data/settings.env优先，已保存的连接/启用状态不会被覆盖。新legacy_tls字段在每个数据源内部管理，不能通过强开全局功能代替配置。

旧部署目录停止本项目两个服务并备份数据后，在上传目录执行：

```bash
unzip cutting-report-kylin-offline-docker-20261103-v2.zip
cd cutting-report-kylin-offline-docker-20261103-v2
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

端口8088、Compose项目和原数据卷保持不变，不执行down -v。Docker位于/data/docker时无需修改项目目录。更新后强制刷新浏览器，再按现场错误选择是否启用旧TLS兼容；本包未提供或猜填BM连接信息，不能把模拟验证当作真实TLS握手或BM业务验收。本包含部署凭据，仅用于指定公司服务器。

发布验证：106项后端定向检查、API生成一致性、前端类型检查及427项发布测试/构建通过。Linux AMD64现有运行镜像禁网ODBC替身验证新增子进程、只读连接选项、参数/Decimal结果传递和父TLS隔离通过；后端用例覆盖失败脱敏、取消、超时、热加载和区间确认。未连接真实BM/SQL Server、生产模型或现场部署；未重复未变更迁移/PDF检查，既有非阻塞构建大包提示保留。

## 20261103-v3

本包基于8c063e7，新增派发历史的日期/机床筛选、每日成功派发趋势及点击日期查看明细；列表与趋势共用时区和首个成功派发时间，排除模拟及未成功记录，不按加工数量重复计数。取消/重试保留首次成功时间，未筛选列表继续保留原完整记录。新查询只读应用SQLite，不调用PDM或魔盒。

新增工人“我的确认记录”，按当前登录账号、明确设备及生产日期查询已有快照，默认10条/页；历史空report_id按最新快照去重，撤销/改派同步改变个人可见记录，任务与确认/审计数据不改写。查询不访问BM或生产库，不接受客户端指定其他工人身份。前端同步生成类型、加载/空/失败、分页和切页取消行为。

相较20261103-v2，依赖锁、Dockerfile、Compose、启动脚本、SQLite结构和迁移编号均未变化，继续复用20261030-v1配套AMD64镜像；package.json仅加入新增功能测试入口，无新依赖。无需重新导入镜像或联网安装。仍包含v2的旧SQL Server隔离TLS兼容和14组既有迁移，从更早版本更新时先备份配置和SQLite，Web/Worker一起更新。

.env仍为145项，与上包逐值一致，覆盖当前示例及代码声明。能管、HR认证、文件库、魔盒、LLM、告警和自动化开启；焊接、SMTP及SQL工作台必要参数仍不完整，对应开关关闭。DATABASE_SOURCES_JSON=[]为默认空登记；现场data/settings.env继续优先，不覆盖网页已保存连接、启用状态或单源legacy_tls设置。BM需按现场实际登记，旧TLS兼容默认关闭。端口8088、原Compose项目/数据卷及Docker存储路径保持不变。

旧目录停止本项目两个服务并备份后，在上传目录执行：

```bash
unzip cutting-report-kylin-offline-docker-20261103-v3.zip
cd cutting-report-kylin-offline-docker-20261103-v3
./scripts/linux/start_offline_docker.sh
docker compose ps
docker compose logs --tail 100 app worker
```

不执行down -v、不删除历史日报或派发/确认记录。升级后强制刷新浏览器，现场检查趋势与明细一致、工人本人记录/设备隔离和撤销改派更新。本包不包含生产数据；包含部署凭据，仅用于指定公司服务器，不公开分发。未在打包中连接真实生产库、PDM/魔盒或执行实际派发。

发布验证：151项后端定向检查（派发趋势、个人确认、派发完整流程、API契约及打包）、API生成类型一致性、前端类型检查及438项发布测试/构建全部通过；覆盖跨时区日期、模拟/失败排除、首次成功时间保留、账号和设备权限、快照去重及撤销改派。未重复未变更的镜像、迁移和PDF测试；非阻塞构建大包提示保留，生产数据规模与真实业务仍需现场验收。
