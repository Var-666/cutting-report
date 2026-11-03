# SQLite 仓储与事务设计

## 目标

`data/metadata.db` 保存系统自身的用户会话、车间日报、确认记录、报表任务、分析缓存、自动化和报表定义。生产事实数据仍只读访问 SQL Server。

本设计解决四个 Repository 各自创建连接、连接参数不一致，以及跨 Repository 写入无法原子提交的问题，并为后续迁移 SQL Server 保留清晰边界。

## 当前实现

### 统一连接

`SQLiteDatabase` 是唯一连接工厂，统一负责：

- `row_factory = sqlite3.Row`；
- `PRAGMA foreign_keys=ON`；
- `PRAGMA busy_timeout=30000`；
- WAL 初始化；
- 成功提交、异常回滚和连接关闭。

`MetadataRepository`、`WorkshopRepository`、`AutomationRepository` 和 `DefinitionRepository` 均继承 `SQLiteRepository`，不再重复实现连接上下文管理器。

### Unit of Work

`UnitOfWork` 在一个 `BEGIN IMMEDIATE` 事务中创建绑定到同一连接的 Repository。绑定 Repository 的方法不会自行提交，由最外层 Unit of Work 统一提交或回滚。

当前原子事务边界：

1. 创建报表任务并关联自动化运行记录；
2. 保存生成报告元数据并将报表任务更新为完成状态。

SQL Server 查询、LLM、HTML 文件写入和 SMTP 发送不得放进 SQLite 长事务。这些外部操作通过任务状态、幂等约束和失败补偿处理。

## Repository 边界

用户、会话、车间日报与确认已分别实现于 `UserRepository`、`SessionRepository`、`WorkshopReportRepository`、`ConfirmationRepository`。车间路由通过 `WorkshopService` 组织业务，运行时认证使用独立用户/会话仓储；设备报表使用日报仓储。

`WorkshopRepository` 只作为既有嵌入式调用的兼容门面保留。新代码不再向该门面添加实现。确认关联与权限冲突必须在数据库事务内检查，不能因分层而破坏原子性。

结构升级统一由编号迁移管理，详见 [API 契约与迁移约定](API契约与迁移约定.md)。

## 扩展原则

- Repository 不向 Service 暴露原始数据库连接；
- 需要跨 Repository 原子写入时必须使用 Unit of Work；
- Repository 内部单步写入可以独立事务执行；
- 外部副作用使用 Outbox 或可重试状态机，不依赖数据库回滚；
- 迁移 SQL Server 时保留 Service 层用例，替换连接工厂和 Repository 实现。
