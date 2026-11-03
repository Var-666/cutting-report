# 多设备隔离修复与升级

更新：2026-10-24。

## 设备边界

- `devices.json` 是设备编号、名称、产线、生产机号、能耗范围、仪表绑定和告警起点的唯一配置来源。两台设备执行相同的 `settings_for_device()`；连接、凭据和共享运行开关仍沿用原配置。
- 启动时逐项核对有效的旧设备环境变量，非空值与目录冲突即停止，错误包含变量及双方值。先按现场依据将有效配置迁入目录，再移除旧变量；不自动改写目录或环境文件。
- 不同设备不得共用生产机号、历史别名或仪表。当前目录只允许一个 `production` 数据源；在该来源内，任务以 `(scope_machine_no, task_id)` 标识。目录唯一映射 `device_id` 与生产范围，不能单独使用 TASK_ID 关联确认记录。
- 设备 API 必须显式指定唯一且有效的 `device_id`，缺失、空值、重复参数及未知设备均返回 422。认证与角色校验保留。用户、连接配置、文件派发目录、报表定义管理及跨设备自动化管理仍为全局接口；自动化计划体内的设备编号必填。
- 数据库固定数据集预览、自定义查询预览和所有设备的自定义报表使用所选设备。自定义生产 SQL 必须声明 `:machine_no`，由服务端固定绑定，表单参数不能覆盖。未声明参数的旧 SQL 需先核实并补齐设备过滤，不自动重写。
- `app.state` 保留共享基础设施与 `devices` 映射，移除默认设备 Service 别名。队列按任务持久化设备选择流水线，流水线拒绝设备不一致的任务。日报、确认、撤销、更正、审计、统计关联及清理均限定生产设备范围。
- 前端从目录取得初选设备，包括初选设备在内的请求均发送编号；目录尚未加载时拒绝设备请求。切换缓存隔离和取消机制沿用原实现。状态、HTML/PDF、告警报告链接均包含所属设备编号。
- 分析缓存保留既有的设备散列键，来源版本按设备保存。历史分析内容及未知展示范围的历史缓存不重写；报表字段和统计口径不变。

## 自动迁移

`build_runtime()` 在原有 `.initialize.lock` 内、仓储初始化和后台启动前执行 `migrate_device_isolation()`。部署前须停止旧 Web 和 Worker。

1. 对已有 SQLite 用 Backup API 创建一致性备份（包含已提交 WAL）：`data/backups/metadata-before-device-isolation-<时间戳>.db`。备份失败即停止。
2. 在同一 `BEGIN IMMEDIATE` 事务中核对归属与父子关系，移除设备列的 Cutter01 默认值，重建确认任务复合主键、日报设备日期唯一索引及分析来源版本主键。
3. 只有明确的单设备历史结构可归属原 Cutter01；确认/审计可从明确的父日报或唯一匹配的快照恢复范围。已有范围必须与父记录一致。孤立、歧义、未知设备、归属冲突、唯一性冲突及外键错误均停止并回滚，保留备份。
4. 将告警游标 `production` 原行改名为 `production:Cutter01`，其他设备统一为 `production:{device_id}`。保留采集编号、成功时间、窗口、两种回查位置、租约所有者及到期时间，不重建事件和报告。新旧游标同时存在时拒绝合并，不取最大值、不重置游标。
5. 最后写入 `schema_migrations` 的 `device-isolation-v2` 标记。结构、数据和标记一起提交或回滚；成功后重复启动不重复迁移或备份。

仅修改应用自有 SQLite，不写外部 SQL Server、HR、PDM 或能管平台。

## 备份与回滚

- **迁移失败**：事务自动回滚。根据生产来源证据核实错误列出的归属，在停机并单独备份后修正范围，再重试；不删除确认、审计、空日报或游标绕过校验。
- **成功后回退版本**：停止 Web 和 Worker；先通过 Backup API 另存当前库，再将升级前备份通过 Backup API 恢复为 `data/metadata.db`，启动与备份匹配的旧代码。不要仅复制活动库主文件或混用旧 WAL/SHM。
- 恢复会回到备份时刻；上线后已有新确认、审计或任务时，必须保留当前库并制定增量恢复方案，不能直接覆盖。
- 告警维护命令须显式给出设备，例如 `python scripts/prepare_alarm_monitor.py --device-id Cutter01`。只读预览不重置；`--apply` 是原有人工重置操作，**不是迁移步骤**，升级时不要执行。
- 能耗诊断命令也须指定 `--device-id`，仪表来自目录；未接入仪表的设备在请求前拒绝执行。

## 本地历史库核查

原 `data/metadata.db` 仅只读核查并复制到临时目录，没有执行原库迁移或修改配置。副本试迁移拒绝以下两份没有归属佐证的空日报：

| report_id | 日期 | item_count |
| --- | --- | --- |
| `wdr_bdeb0503b1994189` | 2026-08-19 | 0 |
| `wdr_3a0c41788a694ffc` | 2026-08-23 | 0 |

它们的 `scope_machine_no` 为空，不能凭当前默认设备猜测。现场确认来源后应记录依据并修正范围，不删除空日报。另有 14 条确认和 1 条确认审计，范围明确为 Cutter01；告警源为 `production` 与 `production:Cutter02`，无目标游标同名冲突。本地未发现旧设备环境变量覆盖；部署主机仍会校验其有效环境。

只读定位空范围：

```sql
SELECT report_id, report_date, item_count, scope_machine_no
FROM workshop_reports
WHERE scope_machine_no IS NULL OR trim(scope_machine_no) = '';
```

## 定向验证

覆盖统一配置与冲突、设备 API 拒绝、同日期和同任务编号隔离、撤销/改派/审计/统计/清理隔离、旧结构及重复迁移、失败回滚、游标和租约保留、报表/AI 缓存/PDF/预览/状态隔离、自动化固定设备、Web/Worker 独立重启恢复，以及前端请求/预加载/导出/批量确认。

只运行相关 Pytest 和 Node 测试；未运行全量、全项目 Lint 或生产构建。PDF 路由使用模拟渲染验证设备及文件边界；真实 Chromium、SQL Server、仪表、邮件及设备派发仍需内网验证。

## 修改文件清单

业务、脚本与配置：

```text
.env.example
backend/app/api/alarms.py
backend/app/api/automation.py
backend/app/api/conditions.py
backend/app/api/database.py
backend/app/api/designer.py
backend/app/api/devices.py
backend/app/api/reports.py
backend/app/api/workshop.py
backend/app/config.py
backend/app/database/alarms.py
backend/app/database/automation.py
backend/app/database/device_migrations.py
backend/app/database/metadata.py
backend/app/database/retention.py
backend/app/database/unit_of_work.py
backend/app/database/workshop.py
backend/app/device_config.py
backend/app/services/application_runtime.py
backend/app/services/automation_service.py
backend/app/services/custom_report_service.py
backend/app/services/device_runtime.py
backend/app/services/pipeline.py
backend/app/worker.py
backend/app/workers/report_worker.py
frontend/src/api/deviceContext.js
frontend/src/views/AutomationView.vue
scripts/check_energy.py
scripts/prepare_alarm_monitor.py
```

定向回归及既有夹具的显式设备上下文调整：

```text
backend/tests/test_alarm_execution_recovery.py
backend/tests/test_alarm_monitor.py
backend/tests/test_alarm_recheck.py
backend/tests/test_automation_recovery.py
backend/tests/test_automation_service.py
backend/tests/test_designer_pipeline.py
backend/tests/test_device_config.py
backend/tests/test_device_isolation_migration.py
backend/tests/test_energy_period_service.py
backend/tests/test_energy_service.py
backend/tests/test_multi_device.py
backend/tests/test_pdf_download.py
backend/tests/test_pipeline.py
backend/tests/test_pipeline_ai_isolation.py
backend/tests/test_prepare_alarm_monitor.py
backend/tests/test_process_coordination.py
backend/tests/test_production_day_consistency.py
backend/tests/test_range_reports.py
backend/tests/test_report_analysis_service.py
backend/tests/test_request_workloads.py
backend/tests/test_retention.py
backend/tests/test_unit_of_work.py
backend/tests/test_web_worker_processes.py
backend/tests/test_worker_observability.py
backend/tests/test_workshop_corrections.py
backend/tests/test_workshop_preload.py
backend/tests/test_workshop_repository.py
backend/tests/test_workshop_snapshot_identity.py
backend/tests/test_workshop_snapshot_sync.py
frontend/tests/multi-device.test.mjs
frontend/tests/report-export.test.mjs
frontend/tests/report-preload.test.mjs
frontend/tests/workshop-batch-confirmation.test.mjs
```

文档：`docs/project/architecture.md`、`docs/project/progress.md`、本文件。
