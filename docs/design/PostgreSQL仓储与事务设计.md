# PostgreSQL仓储与事务

## 连接与仓储

`database/postgres.py`的`PostgresDatabase/PostgresRepository`是正式状态库入口，统一连接、显式事务、执行租约上下文和提交围栏。Web、Scheduler、每个Consumer及Automation各复用一个有界进程池；仓储不自行建池或安装Schema，不接收SQLite文件路径。

各业务Repository保存参数化PostgreSQL SQL，不通过SQLite SQL文本替换。用户大小写比较保留原ASCII NOCASE语义，历史TEXT/JSON/时间及整数布尔按持久化契约处理；需要一致分页的多查询使用相应只读快照事务。

## 事务与副作用

`UnitOfWork`从已有池取得一个连接，各仓储绑定同一连接，不自行提交；最外层`connection.transaction()`定义提交/回滚。内部处理可预期约束冲突时使用嵌套transaction保存点。

先校验参数，再进入短状态事务。生产SQL、HTTP、LLM、SMTP、Chromium和文件操作在事务外执行；保留任务状态、幂等、未知结果及补偿，不能把事务回滚当作外部动作撤销。

## 并发与租约

唯一索引、条件更新、行锁及限定业务身份的事务级advisory lock保证并发约束：确认/日报按物理机床，派发按文档/版本/机床，告警按来源，快照按来源/日期。不得引入全局数据库写锁。

任务通过`FOR UPDATE SKIP LOCKED`领取。Consumer默认1个，可同机多实例；使用`worker:实例ID`租约和每次获取的新owner token。心跳/租约使用PG时钟，执行上下文沿原线程和有界执行器传播。

受保护短事务进入时锁定有效租约行，提交前用真实数据库时钟再次校验；失效引发`ExecutionLeaseLost`并回滚。后台完成同时核对owner、claim_token和attempts，恢复只处理失效owner，不重置有效实例。

## Schema与历史数据

正式结构只通过`database/postgres_migrations/`追加迁移，已发布迁移及其校验和不可改写；普通启动/请求不安装Schema。维护用`cutting_migrator`，应用角色只用`cutting_app`。

旧SQLite升级、导入模块及其CLI已移除。历史数据库文件及PG中的导入元数据保留，不执行双写、自动回退或数据清理。

账号、停机导入、校验、备份及回退见[PostgreSQL迁移说明](../deployment/PostgreSQL状态库迁移.md)，角色与容量见[后台部署](../deployment/WEB_WORKER进程隔离升级说明.md)。
