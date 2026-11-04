# P4 PostgreSQL 状态库与离线迁移

更新：2026-11-04。P4 只准备独立的应用状态目标库；Web/Worker 的 Repository、UnitOfWork、领取与租约仍使用 SQLite。PostgreSQL 不登记到 `DATABASE_SOURCES_JSON`，不替代 SQL Server/MySQL，不复制生产库，不双写。演练副本不会跟随后来恢复的 SQLite 更新。

## 版本、依赖及连接

锁定 PostgreSQL `18.6-bookworm` 及 Compose 中的镜像摘要。当前结构指纹按 PostgreSQL 18 验证，其他主版本明确拒绝。驱动由现有 uv 锁定：psycopg/psycopg-binary 3.3.7、psycopg-pool 3.3.3；三个发行包的 SPDX 均为 LGPL-3.0-only，保持未修改依赖及 wheel 内许可证随 `/opt/venv` 发布。安装采用 `uv sync --project backend --locked --extra sqlserver --no-build`，无 ORM 或第二套 PG 客户端。二进制 wheel 自带客户端库，镜像不额外安装另一套 libpq。

官方依据：[Psycopg 安装与支持平台](https://www.psycopg.org/psycopg3/docs/basic/install.html)、[连接池事务归还](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)、[官方 PostgreSQL 镜像及18版目录](https://github.com/docker-library/docs/blob/master/postgres/README.md)。18版卷挂载在 `/var/lib/postgresql`，由镜像使用其版本子目录。

唯一连接配置为现有 Settings 的分项 `POSTGRES_*`，不另支持 DSN。配置优先级仍为 settings.env > 进程环境 > .env > 默认值，均为冷配置。只被离线工具显式构造的 `PostgresDatabase` 使用；不装配到当前 Runtime，不新增正式运行后端选择开关。

| 参数 | 默认值 / 范围 |
| --- | --- |
| POSTGRES_HOST | postgres；原生工具连接本机时显式设置127.0.0.1 |
| POSTGRES_PORT | 5432；1–65535 |
| POSTGRES_DATABASE | cutting_report |
| POSTGRES_USER | cutting_migrator |
| POSTGRES_PASSWORD | 空；私有配置填写迁移账号密码 |
| POSTGRES_POOL_MIN_SIZE | 0 |
| POSTGRES_POOL_MAX_SIZE | 4；1–32，min不得大于max |
| POSTGRES_CONNECT_TIMEOUT_SECONDS | 5；1–60秒，同时约束池checkout等待；最多32个等待者 |

每个显式数据库对象持有一个有界进程内池，`with PostgresDatabase(settings) as db: with db.connect() as connection: ...` 正常提交、异常回滚并归还连接，退出关闭池；连接checkout做健康检查。P5 应由进程装配一次，不能每个仓储各建池；正式运行时使用cutting_app及其密码，不保留迁移账号作为运行身份。

## Schema 与类型边界

以在临时 SQLite 中实际执行001–016后枚举的39张表为基线，结构描述保存在 `postgres_migrations/sqlite_schema.json`，并非人工挑选几张业务表。PostgreSQL `001_initial` 在独立 `app_state` Schema 中建立：

- 38张应用表，覆盖用户/会话、报表/分析/快照、车间确认、定义/模板、自动化/邮件、下载/派发、告警、能耗、后台任务/调度/租约、模型用量/等待和清理记录。
- 3张独立元数据表：`_postgres_schema_migrations`、`_sqlite_imports`、`_sqlite_schema_versions`。SQLite 的16条迁移历史归档在最后一张表，绝不冒充 PostgreSQL 执行历史。
- 合计41项主键、14项外键（其中1项为来源历史归档关联）、11项普通唯一约束、14项CHECK、316项NOT NULL。97个索引含主键/唯一约束自动索引，以及45个显式索引；包含原有部分唯一索引。

完整应用表名单：admin_role_audits、alarm_incidents、alarm_reports、alarm_source_events、alarm_sync_state、analysis_generations、analysis_source_versions、auth_sessions、background_schedules、background_tasks、cutting_dispatches、cutting_downloads、cutting_file_dispatch_status、email_deliveries、email_delivery_resolutions、energy_daily_snapshots、energy_history_queue、leadership_month_snapshots、llm_usage_calls、llm_usage_totals、llm_usage_tracking、llm_waiters、process_leases、query_tasks、report_analyses、report_definitions、report_jobs、report_schedules、report_snapshots、report_templates、reports、retention_file_deletions、scheduled_report_runs、users、workshop_confirmation_audits、workshop_confirmations、workshop_report_items、workshop_reports。

SQLite INTEGER 映射有符号BIGINT，保留0/1整数布尔及64位ID/计数；REAL映射DOUBLE PRECISION，保留浮点Unix秒。TEXT时间、Decimal文本和JSON仍为TEXT，不转TIMESTAMPTZ/JSONB；TEXT使用C排序规则维持二进制排序。当前基线没有BLOB列，规范化器支持bytes/BYTEA，但新增表/字段仍须追加明确Schema支持，不能静默遗漏。SQLite动态类型中的不兼容值、TEXT中的NUL及NULL主键明确拒绝，不自动清洗。

`users.username` 和 `workshop_reports(report_date, scope_machine_no)` 的NOCASE唯一规则用不可变 `sqlite_nocase()`（只转换ASCII A–Z）表达式唯一索引保留。不是Unicode lower/citext：Ä与ä仍不同。P5 的相应大小写查询必须使用同一函数，不能仅依赖普通 `=`。外键及CASCADE/SET NULL保留，无永久关闭约束。只有llm_usage_calls.id使用BY DEFAULT IDENTITY，导入后在事务内重置下一编号，历史ID不变。

`schema` 在一个事务中使用 PostgreSQL advisory transaction lock 串行安装，失败回滚本批DDL及版本记录。只追加新版本；历史SQL校验和不可变。`status`/导入验证目录结构指纹，包含列、默认值、约束、索引、函数、序列、触发器和RLS状态；版本记录存在不等于结构正确。未知表、结构漂移及工具以外版本拒绝处理，不自动修复。普通业务请求不触发PG建表。

## Docker 准备与权限

私有 `.env` 中填写三个不同密码：POSTGRES_BOOTSTRAP_PASSWORD（仅初始化超级用户）、POSTGRES_PASSWORD（cutting_migrator）、POSTGRES_APP_PASSWORD（未来cutting_app）。不提交这些值。Compose 默认不发布PG端口，app/worker不依赖PG健康状态：

```bash
docker compose --profile postgres up -d postgres
docker compose --profile postgres ps postgres
```

只在全新空卷执行 `scripts/postgres/10-roles.sql`：迁移角色无SUPERUSER/CREATEDB/CREATEROLE、拥有app_state；未来应用角色只有schema USAGE，不具备DDL权限。PG Schema建好后，由迁移账号执行 `scripts/postgres/grant_runtime.sql`，只授予38张业务表DML及用量序列权限，不授予3张迁移元数据表读写。示例由容器管理员通过本地socket执行：

```bash
docker compose --profile postgres exec -T postgres psql -v ON_ERROR_STOP=1 -U cutting_migrator -d cutting_report < scripts/postgres/grant_runtime.sql
```

健康检查同时检查服务可连接和两个非超级用户角色，避免初始化脚本失败后只凭pg_isready误判健康；Web/Worker容器显式清空bootstrap/app初始化专用密码变量。已有卷不会因修改环境变量重新创建账号或更改密码，需管理员明确执行角色维护；失败初始化后也不要对已有数据卷执行自动清空。不能对生产环境使用 `down -v`。profile启动与容器重建均复用postgres_data持久卷。

## 离线演练与重复执行

先备份配置、报告/告警HTML/PDF和下载目录。停止Web/Worker（部署调度器也应保持停止），等待有效Worker租约释放。源文件须在本机；不能从仍写入的其他主机直接复制.db替代备份。工具获取现有Web/初始化/迁移锁、检查进程租约，并保持SQLite EXCLUSIVE写入围栏直到导入/验证结束；任何SQLite写入者都会被排除。然后用官方backup API从已提交WAL状态生成0600一致性备份，实际读取该备份。不会删除或改写源业务数据。

原生Python命令使用私有POSTGRES_*配置（本机PG要设置HOST/PORT）；密码不放命令参数。`--reports-root` 和 `--data-root` 必须指向同批文件卷：

```bash
.venv/bin/python scripts/migrate_sqlite_to_postgres.py schema
.venv/bin/python scripts/migrate_sqlite_to_postgres.py status
.venv/bin/python scripts/migrate_sqlite_to_postgres.py check --sqlite data/metadata.db
.venv/bin/python scripts/migrate_sqlite_to_postgres.py migrate --sqlite data/metadata.db --batch-size 1000
.venv/bin/python scripts/migrate_sqlite_to_postgres.py verify --sqlite data/metadata.db
```

`check/migrate` 可以安装/校验目标Schema；check不写业务数据。verify要求已有成功导入记录，不会补数据。默认备份在源文件同级backups，可用 `--backup-dir` 指定。要验证迁移时点而不是后来已恢复写入的SQLite，可对输出的一致性备份执行verify；同样指定原实体文件根目录。每次校验都明确比较所选快照，不能把旧副本标为当前实时状态。

在Compose中可用 `docker compose --profile postgres run --rm --no-deps app python scripts/migrate_sqlite_to_postgres.py migrate --sqlite /app/data/metadata.db`；重建包含PG依赖及工具的新应用镜像后使用。服务原有启动检查会拒绝锁文件与镜像不匹配的旧镜像。此命令只是工具进程，不启动Web或后台消费者；app/worker需事先停止，PG服务保持运行。

迁移必须面对独立空目标库；其他schema存在业务表也拒绝。不得覆盖、TRUNCATE、UPSERT忽略冲突或自动删数据。重复执行相同内容及源迁移历史的快照会识别已完成记录，重新逐表验证，完全不重复插入；不同快照、目标被改动或摘要不一致则拒绝。重新演练须管理员提供另一个空数据库。P4不自动追赶恢复写入后的SQLite。

## 核对与失败处理

每批1–10000行（默认1000），参数化写入，固定连接/事务，不一次性装载全库。逐表按主键排序，SQLite与PG游标分批读取，用类型标签规范化NULL、64位整数、浮点hex、原始文本和二进制，再计算带长度边界的SHA256；校验全部字段，不仅计数。主键值、NULL、时间文本、JSON空白和状态字串均参与摘要。所有PK/UNIQUE/FK/CHECK在PG中实际生效。

额外输出会话—用户、报表—任务、自动化运行—计划、邮件—运行/报表、告警—事件/报告、确认—人员/车间报表等关联计数；任务/调度键和快照产线/来源维数也核对。软关联原来没有外键的历史孤立记录不猜测修复，原样保存并通过关联计数及全字段摘要核对。

检查数据库引用的报告/告警实体文件；未被标记删除的成功下载按原data/cutting-downloads布局检查。路径越界、缺少文件或不兼容字段拒绝；保留已清理下载的历史标记。不把PDF等二进制导入PG。

JSON结果含真实源版本、PG版本、38张表各自行数/摘要、总行数、关联、文件检查和备份路径。ready只表示预检可迁移；verified表示当前事务内实际逐表核对完成。成功记录与所有业务数据同一事务提交。导入失败包含表名和异常类型，PG回滚未完成导入，不标成功；源及已生成备份保留。连接中断可恢复后重跑，不能根据退出码以外的未经执行检查宣称一致。校验失败时不删除目标，需要管理员调查或提供新空库。

PG备份采用独立备份目录，限制文件权限，保存同批配置/实体文件：

```bash
umask 077
docker compose --profile postgres exec -T postgres pg_dump -U postgres -d cutting_report -Fc > cutting-report-p4.dump
# 恢复到管理员明确创建的空演练库；不要覆盖现有业务库
# docker compose --profile postgres exec -T postgres pg_restore -U postgres -d cutting_report_restore --exit-on-error < cutting-report-p4.dump
```

P4演练后可恢复SQLite服务，PG仅为该时点副本。P5正式切换前再次停写，用最新SQLite及同批实体文件迁入新的空目标库并完整验证；不能直接启用旧演练副本。切换后只能PG权威写入。回退必须停写并恢复同一时点数据库、私有配置及必要文件；切回连接字符串无法恢复PG切换后的业务写入。

## 验收与 P5 具体范围

P4使用隔离的真实PostgreSQL 18.6容器定向验证连接提交/回滚/归还、41表初始化/重复执行/漂移拒绝、跨进程Schema安装互斥、38表关联数据分批导入、ID/时间/数值/NOCASE/唯一性/CASCADE、重复迁移/目标篡改/不同来源拒绝，以及真实中途终止后端连接的回滚恢复。离线围栏覆盖Web锁、其他写入者、有效租约；缺文件和未知源Schema明确拒绝。另实测Compose profile空卷初始化、非超级用户Schema owner迁移、运行账号DML/DDL/元数据权限、容器重建持久化，以及pg_dump/pg_restore到新库后38表46行业务样例的全字段复验。现有SQLite Web/Worker启动恢复测试通过。这是隔离样例数据演练，不是生产数据迁移验收。

真实PG测试可在专用、仅本机开放55434端口且数据库为p4_test的隔离实例中复现：`P4_TEST_POSTGRES=1 P4_TEST_POSTGRES_PORT=55434 P4_TEST_POSTGRES_PASSWORD=<隔离测试密码> PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_postgres_migration.py`。测试账号需在这个一次性实例中创建/删除独立测试数据库和角色；不得使用生产实例。12项用例实际启用执行，未用Mock替代Schema/导入；默认未显式启用时跳过该集成模块。

P5尚需适配的SQLite仓储：Coordination、Metadata、User、Session、WorkshopReport/Confirmation/WorkshopScope（及兼容Workshop门面）、Automation、Definition、Download、Dispatch、Alarm、EnergyReport、ReportSnapshot、LeadershipSnapshot、LlmUsage、Retention。SQL Server PDM/BM和MySQL焊接仓储不属于状态库迁移。

具体工作：将`?`、INSERT OR IGNORE/REPLACE、SQLite upsert/rowid、lastrowid、sqlite_master/PRAGMA和NOCASE查询改为PG等价语法；UnitOfWork绑定同一PG连接并明确隔离/回滚；重做BEGIN IMMEDIATE下的任务领取、租约、续约、恢复、提交围栏与并发唯一性；处理pg_advisory/行锁和SKIP LOCKED等实际领取规则；替换LLM协调状态仓储而保留P3本机SQL/PDF/模型文件锁边界；Runtime统一装配各进程池并正式切换全部状态读写、权限与健康诊断；完成最终停机迁移、单一权威库和同批备份回退验收。P4没有提前实现这些业务适配。
