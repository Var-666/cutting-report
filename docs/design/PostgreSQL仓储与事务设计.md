# PostgreSQL 仓储与事务（P5）

正式状态入口是 `database/postgres.py` 的 PostgresDatabase/PostgresRepository，连接、显式事务、执行租约上下文和提交围栏统一在此处。ApplicationRuntime 在 Web/Worker 各建立并关闭一个池；仓储不接受文件路径，不自行建池或安装 Schema。

UnitOfWork 从现有池取一个连接，各仓储绑定该连接，不自行提交。最外层 `connection.transaction()` 定义提交/回滚边界；内部处理可预期约束冲突时使用嵌套 transaction 保存点。先做参数校验；SQL/HTTP、LLM、SMTP、PDF 和文件操作不放在状态事务中。

PostgreSQL SQL 直接写在各业务仓储，不做 SQLite SQL 文本替换。用户大小写比较延用 P4 sqlite_nocase 的 ASCII 语义，TEXT/JSON/时间和整数布尔保留原持久化类型。分页多查询在必要处使用 REPEATABLE READ；动态条件编译保留 SQL Server 规则，并为应用自有下载记录显式编译 PostgreSQL 参数和 JSON 表达式。

并发不变量由唯一索引、条件更新、行锁或限定身份的事务级 advisory lock 保证：工人确认/日报按物理机床，派发按文档/版本/机床，告警归并按来源，队列接纳按既有容量，单日报快照 CAS 按来源/日期。避免全局数据库写锁。任务领取使用 SKIP LOCKED，实际 Worker 数仍为一个。

租约获取/续约使用 PostgreSQL 时钟与唯一 owner token。执行上下文由原 Worker 线程及有界执行器传播。短状态事务在业务锁之前锁定有效租约行，在提交前再次检查真实时钟，避免暂停后过期提交；失效引发 ExecutionLeaseLost 并回滚。恢复方法额外要求当前 Worker owner。租约只管理数据库执行权，不撤销外部系统已经发生的副作用。

SQLite 只保留 `sqlite_history.py`、`device_migrations.py`、`migrations/` 和 `sqlite_to_postgres.py` 的离线准备/导入用途。正式 Runtime、Worker、健康检查、LLM 等待和用量均不导入它们。001_initial 不改写，新增结构通过002_runtime及未来追加版本显式维护。

切换、权限、数据验证和回退参见 [部署说明](../deployment/PostgreSQL状态库迁移.md)。
