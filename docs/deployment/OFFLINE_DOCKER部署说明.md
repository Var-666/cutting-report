# 麒麟离线 Docker 部署与更新

离线镜像包含 Python、Microsoft ODBC Driver 18、Playwright Chromium、中文字体和应用依赖。麒麟服务器只在首次部署或依赖变化时导入镜像；普通功能更新只替换程序文件。

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

- `backend/requirements.txt`；
- `backend/requirements-sqlserver.txt`；
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
