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

`WorkshopRepository` 暂时作为兼容门面保留，下一阶段按以下职责拆分：

| Repository | 负责数据 |
|---|---|
| `UserRepository` | 本地管理员、HR 工人身份映射 |
| `SessionRepository` | 登录会话及过期控制 |
| `WorkshopReportRepository` | 车间日报和日报任务快照 |
| `ConfirmationRepository` | 工人任务确认及冲突控制 |

拆分时保持现有表结构和公开服务接口不变，先迁移调用方，再移除兼容门面，避免一次性改动登录、日报和确认主流程。

## 扩展原则

- Repository 不向 Service 暴露原始数据库连接；
- 需要跨 Repository 原子写入时必须使用 Unit of Work；
- Repository 内部单步写入可以独立事务执行；
- 外部副作用使用 Outbox 或可重试状态机，不依赖数据库回滚；
- 迁移 SQL Server 时保留 Service 层用例，替换连接工厂和 Repository 实现。
