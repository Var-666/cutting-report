# PostgreSQL 并发与队列容量验证

2026-11-04，P1～P7 稳定性收尾。保留现有连接池、租约、提交围栏和队列实现；本轮只修复 API contracts CI，并增加必要的定向验证。部署方式与总连接预算见[四角色部署说明](WEB_WORKER进程隔离升级说明.md)。

## API contracts CI

`api-contracts.yml` 启动与部署相同摘要的 PostgreSQL 18.6，端口只绑定 runner 的回环地址。管理员库为 `p5_test`，固定密码均标明 test-only；复用 `scripts/postgres/10-roles.sql` 创建 `cutting_migrator/cutting_app`。两者均无 SUPERUSER、CREATEDB、CREATEROLE 权限。

保留原夹具的随机测试数据库及结束删除：管理员只负责创建/清理，迁移账号安装 001～003，应用账号经原授权脚本及 `check_runtime()` 检查后执行业务。没有共享未清理的业务库，也没有放宽运行账号权限。

Python 3.12、uv 0.13.6 及 `backend/uv.lock` 保持不变；本地实际 Python 3.12.14、pytest 8.4.2、psycopg 3.3.7、psycopg-pool 3.3.3。两个契约文件实测收集 43 项，通过 43、失败 0、跳过 0。CI 输出 JUnit，并断言两个模块均收集到测试、数量大于 0、skipped/failure/error 全为 0。另以真实 XML、空集合、跳过及失败 XML 自检检查器，后面三种均被拒绝。pytest 本身失败也会使工作流失败。

## 真实 Consumer 与连接观测

使用独立 Docker PostgreSQL 18.6（本机回环端口 55437、随机测试库），并运行真实 `WorkerProcess` 子进程及各自独立池。每实例同时占用 4 个 operations、10 个 analysis、1 个 ReportWorker 槽，外部阶段保持至少 6 秒，跨越真实 5 秒心跳；租约仍为 30 秒。外部 SQL/模型/PDF 采用可控等待替身，通过真实 P3 共享容量入口，状态前后分别执行 PG 短事务；报表执行原演示数据 HTML 流程。

| Consumer 数 | 每池上限 | 每实例借出峰值 | PG 连接峰值¹ | 池累计等待 ms² | 最长 getconn ms | 最长心跳 ms | 总耗时 s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 4 | 2 | 3 | 21 | 18.826 | 16.022 | 7.298 |
| 1 | 8 | 2 | 2 | 29 | 19.305 | 21.864 | 7.499 |
| 1 | 12 | 2 | 3 | 21 | 13.306 | 15.625 | 7.364 |
| 3 | 4 | 3 | 8 | 59 | 7.169 | 17.838 | 8.257 |

¹ 通过 `pg_stat_activity` 约每 50ms 采样，同库应用连接排除当前观察连接，包含空闲池连接；每实例借出量另由池入口测量。采样峰值不是理论上限。² 各实例池 `requests_wait_ms` 总和；getconn 包含建连及等待，不能等同于纯锁等待。

上述正常场景 PoolTimeout、循环错误、错误租约过期、错误恢复和重复领取均为 0；正常采样未见行锁等待。任务结束连接全部归还、等待队列为 0，无本实例持续超过 1 秒的 idle in transaction，进程退出后其 PG backend PID 全部消失。三实例任务并行达到 45，SQL/单源/单产线峰值均为 1，模型和 PDF 峰值各 1，符合该场景全局 SQL 2、单源/产线 1、模型 2（后台保留原额度）、PDF 1 的共享限制。

初次把 45 个外部 SQL 阶段同时释放时，触发 P3 既有等待队列上限 32，任务按原规则 deferred；没有 PoolTimeout。正常场景改为按 30ms 错峰到达，仍先同时占满所有执行槽，不扩大资源队列。本数据证明受控负载下的连接与租约行为，不代表生产吞吐或能承受任意突发。

默认池上限 4 未出现容量不足的证据，故不改为 8/12/20，不加独立心跳池。现场仍按 Web、Scheduler、Automation、全部 Consumer 合计预算：默认最大 `4 × (N + 3)`，三个 Consumer 为 24，另预留探针、维护连接。不能按线程总数决定池大小。

## 锁竞争与故障注入

- 在受保护事务持有 `FOR SHARE` 时执行续约 `FOR UPDATE`，通过 `pg_stat_activity`、`pg_blocking_pids` 和 `pg_locks` 确认实际 blocker、RowShareLock 及 transactionid 等待。TTL 30 秒、持锁约 1.2 秒时，续约耗时 1.232 秒，事务提交且续约成功；等待随事务结束解除。
- 同样持锁约 1.2 秒，故意将测试 TTL 缩至 1 秒：续约等待 1.228 秒后返回 False，旧事务提交前检查失败并回滚，替代 owner 合法获取租约，非法提交 0。没有移除 `FOR SHARE` 或减弱 Fencing。
- 故意借走池内全部 4 个连接，测试超时设为 1 秒，claim/renew/finish 三项均产生预期 PoolTimeout（1.004～1.005 秒）；该时段未见 PG 行锁等待。归还后 4 个连接全部可用，等待队列清空，新 owner 恢复并完成任务，旧 owner/token 写入被拒绝，非法提交 0。这是故障结果，不能计入正常性能。
- 三个实际 Consumer 同时存活，强杀持任务的一个；不手改租约时间，等待原 30 秒 TTL。约 30.593 秒后空闲实例接管，期间 266 次检查确认其余实例有效、正常任务仍为原 owner/attempts。故障任务最终 attempts=2，正常任务 attempts=1，各完成一次，错误恢复和非法提交均为 0。故障用例使用文件释放信号，避免强杀进程破坏测试自身的 multiprocessing Event。
- 原有未提交续约竞争的 background/report/energy 三类测试及同 owner 同 attempts 的旧 claim token 测试通过，覆盖接管时的提交围栏。

正常续约最长为 21.864ms；故意持锁的有效租约用例约 1.232 秒。故障注入不证明任意长事务都能安全续约：PG 写事务仍不得包含外部查询、模型、PDF、SMTP 等等待。

## 64/256 与实际调度范围

`request_snapshot_refresh()` 的 **64** 是全局 `report_snapshot_refresh` 活动数，包含 pending/running/deferred，不包含 completed/blocked。满额直接 False，不新增任务行；不能描述为“超过 64 自动 deferred”。

**256** 是所有后台任务的 pending 阈值。普通 enqueue 超额仍持久化为 deferred，按 `next_attempt` 和空位重新准入。即使快照活动数少于 64，共享 pending 已满时，成功接受的快照也会是 deferred。两道限制同时存在。

真实 PG 边界测试填满 64 个活动快照，确认拒绝无新行；释放一项后，同一 Scheduler 下一次调度可接受原申请。填入 257 个普通任务得到 pending=256/deferred=1，未释放时不超额准入，完成一项后 deferred 正常补入。既有 blocked 来源不占活动容量、参数/间隔隔离及拒绝后重试测试也通过。页面冷读/过期读后续请求仍可重新申请；Scheduler 对未创建记录继续按到期状态重试，不因拒绝永久推进期限。

当前工作区未提供有效 `PRODUCTION_LINES_JSON`，不能据此宣称现场没有积压。仓库示例目录为两条启用切割线和一条停用焊接线：切割 Provider 的通用 `refresh_plan` 为空，启用焊接且装配快照后每线生成 4 项（首页、日报公共聚合、任务、工件），设备页共用日报。切割继续独立 current/leadership 周期刷新、每日前两个月对账、告警等后台任务；有能耗配置的切割线启动时补最近 12 个月，保存在独立 `energy_history_queue`，通过去重后的 energy_sync 每次处理一项，不能直接算成 12 个通用活动快照。

页面历史日期、不同任务/工件分页筛选会增加按需任务；源库持续慢或 Consumer 停滞也可能使周期任务累积到 pending/deferred。未完成任务不会因保留期限自动删除，本轮未发现确定的拒绝后永久漏执行缺陷，但没有现场目录、队列年龄与拒绝率数据，无法排除生产容量瓶颈。

本次保持 64/256 及准入逻辑不变，不新增容量环境变量。仅在活动快照长期接近 64 且拒绝造成刷新延迟，或 pending 持续达到 256、deferred 长时间不能准入并超过业务时限时考虑调整。先检查 Consumer 数量与槽容量、任务耗时和年龄、SQL Server/MySQL 延迟、SQL/LLM/PDF 配额、PG 连接/锁等待、去重/重试及消费者故障；解决消费停滞后，再依据实测确定阈值，确有必要时才配置化。

## 复现与验收边界

使用原 PostgreSQL 测试夹具，先按 CI 初始化独立 PG18 及测试角色，再执行：

```bash
P5_TEST_POSTGRES=1 P5_TEST_POSTGRES_PORT=55437 .venv/bin/python -m pytest -q -s \
  backend/tests/test_postgres_consumer_stability.py \
  backend/tests/test_queue_capacity_boundaries.py
```

不得把上述端口或固定测试密码用于生产。仅在明确隔离环境开启 `P5_TEST_POSTGRES`；夹具会创建并清理随机测试库。

本轮没有更改业务运行代码、生产查询、统计口径、迁移脚本、锁定依赖、前端或四角色职责。未执行生产迁移、部署及真实 SQL Server/MySQL、LLM、SMTP、能管/Chromium 负载验收；现场须单独核对配置、积压、连接预算和延迟。后续工作为生产迁移、现场联调与具体业务修复，不新增架构阶段。
