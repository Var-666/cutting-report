# Web / Worker 部署、升级与回滚

更新日期：2026-10-24。适用于当前 Cutter01/Cutter02 多设备版本升级到进程隔离版本。

## 运行边界

- `app`：一个 Uvicorn Web 进程，负责页面、鉴权、生产只读查询、任务提交、状态和按需 AI/PDF。
- `worker`：独立 Python 进程，负责报表、自动化、SMTP、告警、周期分析、能耗与清理。Web 重启不重置其任务。
- 两者使用同一配置、设备目录和本机的 data/reports/logs 卷。SQLite 继续使用 WAL；生产 SQL Server 保持只读。不得把这些卷放在 NFS/SMB 或分布到不同主机。
- 当前不支持多个 Web；HTTP 请求取消仍属于唯一 Web 进程，启动锁会拒绝第二个实例。Worker 采用持久化独占租约，第二个消费者不会接管有效租约。

## 首次升级

1. **先停止旧单进程容器或原生服务。不能让旧代码与新 Worker 混跑。** Compose 使用 `docker compose stop`，保留原项目名和卷，不加 `-v`。
2. 备份 `.env`、data/settings.env、完整 data 卷和 reports 卷。确认 SMTP 的 sending/unknown、魔盒上传/指派/取消未知记录，未知结果不得为了升级而改成可重试。
3. 更新源码构建部署使用 `docker compose up -d --build`。离线部署使用原 `compose.yaml` 加新版 `compose.offline.yaml`；两服务都挂载当前 backend/vendor，Compose 显式提供 PYTHONPATH，现有依赖镜像可以继续使用。
4. 首次迁移会在 data/backups 写入 `metadata-before-processes-*.db`，使用 SQLite backup，权限 600。已有设备迁移备份逻辑仍保留。新增协调表、分析执行记录及 report_jobs/email_deliveries/energy_history_queue 的可空 execution_owner 字段，不改原字段含义或删除记录。
5. `docker compose ps` 应看到 app 和 worker 各自 healthy。Worker 初次启动等待 Web healthy，仅用于启动排序；重启 Web 不会连带重启 Worker。
6. 核对两台设备的历史报告、确认与审计，提交一份报告检查完成状态、HTML/PDF；核对告警和能耗游标。真实邮件/魔盒操作应按既有现场验收流程单独核对。

常用命令：

```bash
docker compose logs --tail 100 app worker
docker compose restart app
docker compose restart worker
docker compose exec worker python -m app.worker --health
```

Web 重启不会恢复或重排后台任务。Worker 非正常退出后，等待旧租约失效（通常最多 30 秒）再启动；不要手工清除有效租约来制造并发消费者。正常停止会在消费者结束后释放租约；在途操作未结束时留待租约到期和围栏处理。

修改 data/settings.env 的自动化/SMTP 参数通过文件版本传播，下一轮操作使用新快照，已开始的操作保留原快照。修改宿主机 .env 或数据库、LLM、设备配置，需要重建/重启**两个**服务，保持配置一致，例如 `docker compose up -d --force-recreate app worker`。

## 原生部署

分别注册两个独立系统服务，工作目录均为项目根目录。Web 保持 --workers 1；不要用增加 Web workers 代替后台进程。

```bash
./scripts/linux/start_kylin.sh
./scripts/linux/start_kylin.sh worker
```

Windows 在两个终端/服务中分别执行：

```text
scripts\windows\start_windows.bat
scripts\windows\start_windows.bat worker
```

也可直接设置 PYTHONPATH=backend 后执行 `.venv/bin/python -m app.worker`，Windows 使用对应虚拟环境 Python。Windows 原生文件锁与服务管理仍需现场验证，Docker/Linux 路径已作隔离回归。

## 资源与恢复

- Web 保留原 AI/PDF/SQL/鉴权独立限额 10/2/8/8。模型再使用跨进程共享槽位，总量受同一个 LLM_MAX_CONCURRENT 控制；不提高原上限。普通后台受半池配额限制，周期任务可利用全部空闲容量，真实前台等待优先。
- 本地查询/领导快照缓存保留，Worker 刷新发布设备级版本标记以触发 Web 缓存换代；能耗快照通过旧值比较防止并发结果回退。下载保留清理和 Web 推送后删除共享 OS 锁，保留完整清理审计。
- Worker 使用报表/自动化/邮件各一个消费者、4 个后台操作执行者、10 个分析执行者，不随设备数量增加执行线程。告警保留原租约续期线程，数量受操作池约束。
- SQLite ready 队列最多 256 个 pending；超额保存为 deferred/QUEUE_CAPACITY，失败保存类型并按 30–3600 秒退避。执行池没有额外等待队列，任务只在有空位时认领。deferred 属于磁盘持久化积压，不占执行线程；持续过载时需监测 background_queue 和数据卷空间，不能将 256 误解为整个数据库只会保存 256 条任务。辅助任务完成记录、非活动分析尝试保留 30 天，未完成任务不清理。稳定 OS 锁文件不在线删除，避免锁文件换 inode 后出现两个持有者。
- Worker 租约每 5 秒续 30 秒。后台仓储在进入及提交写事务时检查执行者，失效操作回滚；接管者恢复遗留报表、后台任务和能耗认领。报表使用不同执行者的内部路径，迟到文件不覆盖新文件，公开下载名称和链接协议保持。
- 邮件 sending 恢复成 unknown，接受地址、Message-ID 和核对记录保留，不自动重发。魔盒仍由单 Web 的既有事务/CAS 状态机保护；Worker 不恢复或重试其上传、指派和取消。外部系统的最终结果仍可能需要人工核对，不能承诺外部副作用恰好一次。

## 健康语义

`GET /api/v1/health` 保留 success、data.status、workers 等主字段；HTTP 200 表示 Web/SQLite 可用，后台不可用通过 background_ready=false 和 background_status 表达。自有 SQLite 不可访问仍返回 503。后台状态为 missing/running/stalled/lost/stopped/unavailable，并提供心跳年龄、进展、队列计数。Web 的状态读取使用独立线程容量和 2 秒缓存。

Worker 使用独立 `--health` 探针，检查有效心跳、消费者、队列观测和停滞状态。存活不代表有进展；超过 REPORT_WORKER_STALL_TIMEOUT_SECONDS 的任务会标记 stalled。进程挂起超过租约期限后为 lost，旧执行者不能续租复活。

## 回滚

本次迁移是增量迁移。回退到**本次改造前的当前多设备版本**时，先停止 app 和 worker，保存升级后的数据，再启动原单进程版本；旧代码可忽略本次新增表和可空字段，原报告路径保存在数据库内，已成功记录无需还原到旧备份。

回滚前应排空新增后台队列；未排空的 pending/deferred 记录应保留，旧版本不会消费，恢复新版后才能继续。SMTP/魔盒未知结果继续保持未知，不因回滚改为 pending。更早的多设备迁移之前版本仍受原唯一约束兼容限制，不能据此直接回退。

只有首次迁移失败、且确认升级后没有新增业务记录时，才考虑停止全部进程并恢复升级前数据库备份。已经接收业务后恢复旧备份会丢失新增确认、报表和审计，不能作为常规回滚方式。不得清空卷或在线删除协调锁来回滚。

## 验证边界

定向回归使用真实 spawn 子进程验证 Web 无后台线程、Worker 独立消费、Web 连续重启、Worker 强制退出恢复、任务原子认领、旧执行者写回围栏、全局模型容量与单飞优先级、配置传播和 SQLite 崩溃回滚。独立 Docker 项目使用新卷、演示数据、现有依赖镜像与只读源码挂载验证双服务、两设备 HTML、真实 Chromium PDF 和分别停止/重启。

未调用真实生产写操作、真实 SMTP 投递或魔盒派发；生产 SQL/能管/LLM 的吞吐、驱动异常、部署资源和 Windows 原生双服务仍须内网验收。未默认执行全量测试或重建前端/完整镜像。


本次实际执行的定向验证（均通过；按改动分批执行，未重跑全量）：

| 范围 | 测试文件或命令 | 已验证结果 |
| --- | --- | --- |
| 多进程与调度 | `test_process_coordination.py`、`test_web_worker_processes.py`、`test_worker_scheduler.py` | spawn 进程、认领围栏、崩溃恢复、队列、迁移备份、独立重启、配置、模型/PDF 容量与清理审计 |
| 缓存及生产查询 | `test_query_service_cache.py`、`test_workshop_preload.py`、`test_leadership_report_service.py` | 33 项通过；另以子进程发布设备级版本验证 Web 缓存换代和设备隔离 |
| 配置与租约补充 | `test_automation_runtime.py`、`test_runtime_settings_consistency.py`及配置版本、协调和热传播用例 | 本轮组合 29 项通过 |
| 受影响集成 | `test_request_workloads.py`、`test_unit_of_work.py`、`test_alarm_execution_recovery.py`、`test_multi_device.py`、`test_email_resolution.py`、`test_automation_missed_cycles.py`、`test_retention.py`及健康用例 | 本轮组合 61 项通过 |
| 外部未知结果保护 | `test_cutting_dispatch.py -k 'unknown or interrupted or restart or duplicate or concurrent'`，SMTP 接管与旧 owner 写回用例 | 魔盒 14 项通过；SMTP 不重发与围栏通过，未真实投递 |
| 下载文件清理 | `test_file_downloads.py -k 'cleanup or pushed or delete'`及双进程推送后删除/保留清理用例 | 4 项已有回归及真实子进程互斥回归通过 |
| 保留的分析/报表链 | `test_report_analysis_service.py`、`test_llm_capacity.py`、`test_llm_retry.py`、`test_current_report_refresh_worker.py`、`test_pipeline.py`、PDF/能耗/邮件相关定向文件 | 分批回归通过，保留四段分析、缓存、周期强制调用、报表格式及异常恢复 |
| 部署/包 | `test_docker_package.py`、`docker compose config --quiet`（常规及离线组合）、`bash -n`（两个 Linux 启动脚本） | 通过；离线包包含新入口和升级文档 |
| 真实 Docker | 独立项目 `cutting-worker-isolation-test`，现有 amd64 依赖镜像加当前源码，只读挂载、新演示卷 | 两服务 healthy；两设备 HTML 与 354573 字节真实 Chromium PDF；Web/Worker 分别停止和重启，报告保留；Web 停止期间 Worker 完成队列任务 |

本机保留既有 Starlette TestClient/httpx 弃用警告。Docker 在 arm64 主机运行已有 amd64 镜像，已通过上述测试，但不据此宣称生产架构吞吐。测试容器和专用卷在验证后清理，未变更正式部署。
