# Web、Scheduler、Consumer、Automation 部署、升级与回滚

更新：2026-11-04，P6。状态库为 P5 PostgreSQL；当前只有一个 Consumer，不支持多消费者或多主机部署。

## 进程与职责

| Compose服务 | 入口 | 职责 / 租约 |
| --- | --- | --- |
| app | app.main:app，Uvicorn workers=1 | HTTP、鉴权、查询、持久化任务提交与状态；原Web启动的下载/派发恢复保留 |
| scheduler | python -m app.scheduler | 生产周期刷新、告警、能耗、历史对账、清理计划入队；独占scheduler租约 |
| worker | python -m app.worker | 唯一Consumer，领取后台任务与报表；独占worker租约 |
| automation | python -m app.automation_worker | 自动化计划/补偿、报表任务准备关联、邮件发送；独占automation租约 |
| postgres | PostgreSQL 18.6 | 统一业务状态、队列、完成时间、租约和心跳 |

Scheduler不调用业务执行、模型、PDF或SMTP；Consumer没有周期调度循环；ReportWorker只生成报表，保留完成/失败回调。Automation中的计划扫描和邮件发送使用两个原有职责线程，慢源准备和慢SMTP互不阻塞。Scheduler不扫描自动化计划。各角色通过PostgreSQL衔接，不共享Python内存，不保留切回混合Worker的开关。

所有角色使用同一配置与同机data/reports/logs卷，继承P1统一产线与来源Registry、P3 SQL/LLM/PDF本地共享锁。后台进程不开HTTP端口；仍只允许单Web与单Consumer。配置文件及报表/附件继续在原卷，不迁到新目录。

## 旧部署升级门槛

**P5 PostgreSQL正式现场切换尚未执行。旧SQLite部署必须先完成P5停机、最新一致性备份、全部状态导入与校验，再部署P6。不能直接启动三个新后台容器处理尚未迁移的库。** 具体使用 [PostgreSQL状态库切换](PostgreSQL状态库迁移.md)，禁止用P4旧演练副本替代最新迁移数据。

1. 停止旧Web、混合Worker及其系统服务/重启策略，保留Compose项目名和命名卷，不执行`down -v`。
2. 保留同一时点PostgreSQL备份（SQLite旧部署还须原库一致性备份）、.env、data/settings.env、完整data/reports文件；核对SMTP sending/unknown和魔盒未知结果，不能改成自动重试。
3. 用维护账号明确安装/检查已有`002_runtime` Schema，P6未新增表或修改历史迁移。正式角色只用无DDL权限cutting_app，不在启动时自动建表，不回退SQLite。
4. 更新同一应用镜像和四个应用进程源码，按下方命令启动。三个后台只依赖健康PostgreSQL，不依赖Web或彼此先启动；顺序任意。有效的旧worker租约必须释放或自然到期。
5. 分别检查三个后台探针、Web健康接口和配置诊断；核对已有历史报表、确认、告警/能耗队列与自动化运行。外部SQL/模型/SMTP/能管和生产副作用必须另行现场验收。

## Docker与离线

一个应用镜像供四个启动命令使用，PostgreSQL单独镜像。首次部署Schema和旧数据迁移完成后：

```bash
docker compose up -d --build app scheduler worker automation
docker compose ps
docker compose logs --tail 100 app scheduler worker automation
```

宿主机`.env`改变要重建四个应用容器：

```bash
docker compose up -d --force-recreate app scheduler worker automation
```

管理页产线/连接及自动化/SMTP仍在下一次操作热加载；冷配置和SQL容量须四个进程同时更新/重启。Scheduler加载新增/停用产线与来源变化；Consumer在新操作边界选快照，旧来源任务不写新快照；Automation加载同一文件及SMTP快照。不能假定一个进程改了内存其他进程就生效。

离线仍使用 `compose.yaml + compose.offline.yaml` 和 `scripts/linux/start_offline_docker.sh`，统一预检四个角色使用相同依赖镜像，再检查PG现成Schema，最后启动四个应用容器。四角色均挂载当前backend/vendor；无需四个不同镜像。锁文件/依赖不匹配须重建导入镜像，普通源码变动可复用匹配依赖镜像。不要输出完整`docker compose config`或私有配置。

```bash
docker compose -f compose.yaml -f compose.offline.yaml up -d --no-build --pull never --force-recreate app scheduler worker automation
# 任一角色可单独重启，不重置其他角色
docker compose restart scheduler
docker compose restart worker
docker compose restart automation
```

## 原生服务与连接池

分别注册四个系统服务，工作目录同为项目根目录：

```bash
./scripts/linux/start_kylin.sh web
./scripts/linux/start_kylin.sh scheduler
./scripts/linux/start_kylin.sh worker
./scripts/linux/start_kylin.sh automation
```

亦可用`PYTHONPATH=backend .venv/bin/python -m app.scheduler|app.worker|app.automation_worker`。不要把这些命令串成一个系统服务或用多Uvicorn workers替代。

每个进程独立复用PostgresDatabase池，默认每池0–4，总上限16；不按产线新建状态池。Scheduler主循环只使用短状态事务，Consumer仍是4操作线程、10分析线程和1报表线程，Automation为2业务线程及心跳；状态池不必等于执行线程数。生产SQL、LLM、PDF、SMTP在领取/状态提交事务外执行，不长期占PG连接或行锁。进程退出关闭本进程池及来源客户端。P3外部SQL全局12/单源/单产线限制及LLM前台优先、Chromium锁保持。

## 租约、恢复及故障

三个独立租约都为30秒、5秒续约，以PG数据库时钟判有效。同角色第二实例拒绝启动；角色之间不竞争同一租约。execution_scope按真实角色设置，事务进入与提交都核对并持有租约共享锁；旧owner不能提交。数据库短暂不可用不回退，未持久化工作不标成功；租约过期停止受保护工作，恢复由当前有效角色重新接管。租约无法撤销已发出的外部副作用。

- Scheduler重启从持久化期限恢复P2内存提示，保留每日对账和队列去重；消费者可继续处理已入队任务。快照下次期限读取任务实际完成时间+间隔；Consumer不再修改Scheduler内存。未到期仍跳过数据库，不每秒全表扫描。
- Consumer取得worker租约后恢复后台任务、报表、能耗队列；热重建不回收同owner在途能耗claim。待提交能耗结果只由Consumer刷新，Scheduler仅检查到期并入队。完成/失败继续原状态及退避；失效owner不得重复完成。
- Automation取得automation租约后独占恢复SMTP sending为unknown，禁止自动重发。计划reserve/报表attach与邮件唯一性继续由原仓储事务保证；Consumer回调丢失时Automation从已持久化完成报表补偿，回调与扫描并发只生成一条outbox。SMTP成功但状态写失败保持sending，接管后unknown，需既有人工核对。
- 下载、派发恢复仍由Web负责，没有移动到新后台角色。

各角色崩溃不删除持久队列。角色被强杀后同角色须等租约失效再接管，不能直接篡改生产租约或重发unknown邮件。

## 健康与配置诊断

```bash
docker compose exec scheduler python -m app.scheduler --health
docker compose exec worker python -m app.worker --health
docker compose exec automation python -m app.automation_worker --health
```

每个探针只核对本角色PG实际租约、心跳、循环/执行线程及停滞状态，不依赖另一个角色健康。Web `/api/v1/health`保留原字段，HTTP200表示Web/PG可用，后台任一角色异常时background_ready=false、background_status=degraded，`workers.roles.scheduler/worker/automation`给出各自状态。PG不可用返回503。状态读取缓存2秒，missing/lost/stopped/stalled/unavailable分开，公开响应递归移除内部配置指纹。管理员配置诊断增加Scheduler/Automation快照，按各自owner比较实际值，缺失/过期不算已生效；资源诊断分进程显示，不伪装全局计数。

## 回退与验证边界

P6回退P5前必须停止三个新后台角色，保存同批PG/配置/文件，再只启动一个旧混合Worker，不能新旧并跑。P6未改PG结构，保留现有任务/邮件未知状态；更早SQLite版本不能只换连接字符串回退。生产停机、状态迁移和正式切换需单独授权。

本轮定向验证包括真实PG三角色互斥/围栏、三种启动顺序、逐角色强杀接管、P2到期缓存/热加载/快照期限、能耗在途claim保留、并发outbox补偿、邮件未知结果恢复及诊断脱敏。Docker使用现有amd64运行镜像加锁定依赖、当前源码只读挂载和独立演示卷，分别验证三个实际模块入口healthy、Scheduler单独停止/重启、PG短暂暂停后的拒绝与恢复；不是生产部署成功或吞吐压测。外部SQL/能管/模型/SMTP现场联调未执行。
