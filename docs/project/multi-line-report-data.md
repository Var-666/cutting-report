# 多产线报表缓存与快照

本文只描述当前缓存、查询与刷新规则。产线配置见[配置参考](../deployment/配置参考.md)，焊接指标见[焊接接入](welding-integration.md)，进程与事务见[架构](architecture.md)。

## 共享能力与身份

- `ProductionReportProvider`提供`report_types/source_identity/fetch_summary/fetch_detail/refresh_summary/refresh_plan`；业务查询与返回类型由各产线保留，不统一切割和焊接指标。
- `QueryCache`保存有界进程缓存，`CacheRevision`在同机共享卷发布失效修订，`ReportSnapshotService/ReportKey`通过PostgreSQL保存完整快照。切割领导月报、日报确认与能耗继续使用其专属业务结构。
- 快照键包含产线、类型、日期、规范化筛选、必要分页/排序、来源、schema和权限范围。配置切换后新操作使用新源键，在途旧操作只写旧键；设备/机器人/工艺/状态代码0都是有效条件。
- `source_identity`用于来源隔离，`CacheRevision`仅失效进程缓存，存储`data_version`校验完整载荷。焊接公开业务版本仍由`WeldingService.version`生成，不能用存储校验和替换。

## 页面查询

| 情况 | 行为 |
| --- | --- |
| 有效缓存或快照 | 返回对应条件结果，不查询MySQL；保留真实截止时间和来源 |
| 过期但未淘汰 | 返回旧结果并标记stale，提交去重刷新；失败保留结果 |
| 从未生成 | Web向原队列提交刷新并返回准备状态，不伪造零值或借用其他条件结果 |
| 导出与焊缝下钻 | 现场读取源库，保留版本、权限和错误校验 |

焊接日报和设备页共用规范化`page:daily`快照，其中包含有界的完整按日、设备/机器人聚合数组；响应按当前分页/排序投影，保留原`data_as_of/refreshed_at/expires_at`及刷新状态。不能把此方式用于未完整保存明细的任务或工件页。

任务/工件页继续按分页和排序隔离，统计、总数及当页明细在同一MySQL只读事务内计算；总数复用相同谓词，不混合旧统计与实时明细。页面和默认预热每页10条，首页保留最多10条最近作业。

源水位和设备目录沿用有界采样，至少30秒；`source_info_as_of`单独标记采样时间。`MAX(update_date)`是辅助信息，不作可靠CDC，不把复用水位标为刚查询。

## 后台刷新与前端

Scheduler按Provider计划提交首页近7天、当日日报公共聚合、任务、工件四个预热任务；设备页复用日报，不单独预热。其他日期、筛选、页码和排序按实际查询生成，不扫描全部组合。

Consumer复用原持久队列、产线锁、来源检查、去重与失败退避，不增加常驻线程或第二套缓存。任务提交时间`requested_at`与持久化完成时间参与原刷新合并；失败不覆盖旧快照，成功后发布跨进程失效修订。

前端复用`reportCache`，规范化参数与默认值；已有数据时静默刷新并保留更新时间，失败显示原刷新失败状态，成功后自动替换。新条件准备按既有限次数轮询，切换产线、条件或离开时取消旧等待；不应用未提交草稿、不重复改写URL。

配置快照与后台协调器的页面读取使用独立有界`snapshot_requests`，源查询或嵌入式回退仍用`welding_requests`。队列满、执行池排队、连接池耗尽、源查询失败分别处理，不扩大自动重试。

## 新鲜度、容量与验证边界

`REPORT_BACKGROUND_REFRESH_SECONDS`决定近期刷新间隔，`LEADERSHIP_REPORT_HISTORY_CACHE_SECONDS`决定历史有效期，`REPORT_DATA_CACHE_SECONDS`控制L1；具体默认值与热加载见[配置参考](../deployment/配置参考.md)。TTL、显式刷新、后台刷新、来源及格式共同决定有效性。

快照保存使用短PG事务并校验执行租约，外部SQL不占状态写事务；有界淘汰和现有64/256队列策略保留。没有快照的源库故障不能返回假空数据；有快照也必须标明截止时间与过期状态。

仓储、隔离与并发回归位于`test_report_snapshots.py`、`test_welding_snapshots.py`及相关产线/缓存测试，前端行为在`frontend/tests/welding.test.mjs`。现场性能需要真实表结构、规模和EXPLAIN依据，本地合成数据不能推导生产提升比例。

Schema、备份和回退统一见[PostgreSQL迁移说明](../deployment/PostgreSQL状态库迁移.md)，不通过手改队列或删除Schema版本回退。
