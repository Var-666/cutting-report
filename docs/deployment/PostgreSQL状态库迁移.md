# PostgreSQL 状态库切换（P5）

应用状态统一使用 PostgreSQL 18；切割/HR/PDM 仍查询 SQL Server，焊接仍查询 MySQL。不把状态库登记到 `DATABASE_SOURCES_JSON`。保留一个 Web、一个 Worker，使用同机文件卷保存报告、下载文件和容量锁。

本说明是维护操作步骤，不代表已经执行生产切换。首次从 SQLite 升级必须停机迁移；后续启动不会自动建表、导入或回退 SQLite。

## 连接与账号

沿用已锁定的 PostgreSQL `18.6-bookworm` 镜像摘要、psycopg 3.3.7 和 psycopg-pool 3.3.3。无需 ORM。生产启动校验账号、迁移脚本校验和和实际 Schema 指纹。

| 配置 | 用途 |
| --- | --- |
| `POSTGRES_HOST=postgres` | Compose 内部服务地址；原生部署显式配置实际地址 |
| `POSTGRES_PORT=5432` | 内部端口，Compose 不发布到宿主机 |
| `POSTGRES_DATABASE=cutting_report` | 独立应用状态库 |
| `POSTGRES_USER=cutting_app` | Web/Worker 固定运行账号 |
| `POSTGRES_PASSWORD` | cutting_app 密码 |
| `POSTGRES_BOOTSTRAP_PASSWORD` | 仅首次初始化的 postgres 管理员密码 |
| `POSTGRES_MIGRATOR_PASSWORD` | cutting_migrator 迁移账号密码 |
| `POSTGRES_POOL_MIN_SIZE=0` | 每进程池的最小连接数 |
| `POSTGRES_POOL_MAX_SIZE=4` | 每进程池的最大连接数，允许1–32 |
| `POSTGRES_CONNECT_TIMEOUT_SECONDS=5` | 连接及池等待超时，允许1–60秒 |

三个账号使用不同的非空密码。旧 P4 私有配置若将 `POSTGRES_PASSWORD` 用作 migrator 密码、将 `POSTGRES_APP_PASSWORD` 用作 app 密码，需要在升级前分别转移到 `POSTGRES_MIGRATOR_PASSWORD`、`POSTGRES_PASSWORD`，并把 `POSTGRES_USER` 改为 cutting_app。不要直接把同一个密码复制到三个变量。

`data/settings.env` 优先于进程环境和 `.env`；升级前检查其中的 POSTGRES 配置，防止旧的迁移账号覆盖运行账号。PG连接配置为冷配置，修改后同时重建 Web、Worker。迁移进程显式使用 cutting_migrator；Web/Worker 的容器环境清空初始化/迁移专用密码。

已有 PG 卷不会因为修改 `.env` 自动修改账号密码。P4 演练库不跟随后来 SQLite 的变化，不能直接当作最终生产目标。最终切换必须使用新的空目标，或严格验证与最终源快照一致的已导入目标；工具拒绝覆盖不同来源。

## 初始化与 Schema

保持原 Compose 项目名 `cutting-report` 和三个文件卷，增加必需的 `postgres_data` 卷，挂载到 PostgreSQL 18 的 `/var/lib/postgresql`。不要在新目录随意改 Compose 项目名，否则会连接到另一套空卷。

```bash
# 先导入与当前依赖锁匹配的应用镜像，以及指定版本的 PostgreSQL 镜像。
# 有网络的构建机可以构建；内网服务器使用 docker load 导入。
docker compose -f compose.yaml -f compose.offline.yaml up -d --no-build --pull never postgres

docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  python scripts/migrate_sqlite_to_postgres.py schema
```

新空卷通过 `scripts/postgres/10-roles.sql` 创建两个非超级用户角色和 `app_state`。`schema` 使用 cutting_migrator 显式安装迁移，再执行 `grant_runtime.sql`。Web/Worker 只校验现成结构，不能使用管理员或迁移角色启动。

已发布的 `001_initial` 保持不变。`002_runtime` 为分析/快照增加稳定插入序号，为同产线同类运行任务增加部分唯一索引，并提供兼容历史派发时间文本的解析函数。P7追加003_consumer_scaling的owner查询索引与后台claim_token列，001/002保持不变；应用启动不执行迁移。004另保留1张退役审计表；当前包括38张正式业务表、1张历史归档表、3张迁移元数据表、102个索引；迁移元数据对 app 只读，三个身份序列仅授予必要使用权限。运行账号无 Schema CREATE、SUPERUSER、CREATEDB、CREATEROLE 权限。

## 首次停机切换

先保留上一版本程序、镜像、私有配置及 Compose 文件。备份文件卷和 SQLite，不能只复制一个仍在写入的 `.db` 文件。以下命令只停止本项目的 Web/Worker，不停止公司其他项目：

```bash
docker compose -f compose.yaml -f compose.offline.yaml stop app worker

# 按实际维护目录调整；请先检查备份盘可用空间。
mkdir -p /data/cutting-p5-backup
chmod 700 /data/cutting-p5-backup
cp -p .env /data/cutting-p5-backup/env.before-p5

docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  sh -c 'mkdir -p /app/data/backups && tar --exclude=data/backups -czf /app/data/backups/p5-files-before-switch.tar.gz -C /app reports data'
```

该备份包含报告及完整应用文件目录，并排除备份目录自身；命令失败不能当作备份完成。报告和下载量大时，应使用现有备份流程在大盘保存同批实体文件。不要覆盖原数据卷、不要执行 `down -v`。

完成 Schema 准备后，先执行离线旧库准备：该步骤除了升级到001–016，还按现场统一配置完成原P1的告警来源、最新分析和源版本归属整理，不能依赖新 Runtime 启动补写。

```bash
docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  python scripts/migrate_database.py upgrade --database /app/data/metadata.db
```

配置不完整、归属冲突或仍有有效进程时停止操作；保留输出的备份，不猜测设备。然后使用同一文件卷中的最终 SQLite 数据：

```bash
docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  python scripts/migrate_sqlite_to_postgres.py check --sqlite /app/data/metadata.db

docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  python scripts/migrate_sqlite_to_postgres.py migrate --sqlite /app/data/metadata.db --batch-size 1000

docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  python scripts/migrate_sqlite_to_postgres.py verify --sqlite /app/data/metadata.db
```

工具持有旧 Web/升级文件锁、检查有效 Worker 租约并排除 SQLite 写入者，使用官方 backup API 产生0600一致性备份；不会删除源数据。保留的离线升级入口为 `scripts/migrate_database.py`，用于尚未达到 SQLite 001–016 基线的旧库，必须在停止旧版进程后先运行。

`check` 验证 Schema、字段类型、业务关联、文件引用和空目标；`migrate` 在一个 PG 事务内按依赖顺序分批导入38张业务表及存在时的退役审计表，保留16条编号迁移历史及已有的 device-isolation-v2 历史标记；`verify` 重新核对每张表的行数、全部字段摘要、关联、文件和插入顺序，不修补差异。同一快照可重复校验，不同来源或目标非空时拒绝覆盖。历史 ID、密码哈希、路径、设备/来源身份不改写。原有隐式 rowid 顺序显式导入两个 insertion_order 列，并重置三个身份序列。

只有 `migrate` 和 `verify` 均显示 `result=verified` 才启动：

```bash
./scripts/linux/start_offline_docker.sh
docker compose ps
docker logs --tail 100 cutting-report
docker logs --tail 100 cutting-report-worker
curl --fail http://127.0.0.1:8088/api/v1/health
```

启动脚本先检查依赖镜像和已准备的 PG Schema，不自动执行 DDL/导入。Web 依赖 PG 健康，Worker 依赖 PG 和 Web 健康。核对两个应用容器均正常、后台就绪、用户登录、历史报表/告警/确认、文件路径、自动化计划和用量；现场另行验证 SQL Server/MySQL、能管、魔盒、SMTP、LLM。不要用“重启后没报错”代替数据校验。

原生部署使用相同工具及显式环境变量：运行服务用 cutting_app；维护终端只在迁移期间设置 `POSTGRES_USER=cutting_migrator` 和对应 `POSTGRES_PASSWORD`，不把密码放命令参数或共享脚本。

## 事务、租约与故障

每个进程只装配一个 PostgresDatabase，所有状态仓储和 UnitOfWork 共用该进程连接池。跨仓储写入由外层事务提交/回滚，已知唯一性冲突使用保存点，不能提前提交外层事务。确认按设备、派发按文件/版本/机床、告警按来源等真实业务身份使用短事务锁；不锁住所有写入。

任务领取使用 `FOR UPDATE SKIP LOCKED`，更新状态与 owner 在同一事务内；dedup_key 和同产线同类运行任务索引保证去重/互斥，保留现有容量、重试和调度间隔。Worker 获取租约用原子条件 UPSERT，每次获得新的 owner token；续约/接管使用数据库时钟。

受保护事务先对当前有效租约行取共享锁，再执行业务写入，并在真正提交前用 clock_timestamp 再验期限。锁仅覆盖短状态事务，不包含 SQL Server/MySQL/LLM/SMTP/文件操作；续约和接管需等待该短事务结束。过期或已被接管的执行者回滚，只有有效持有者可恢复后台状态。邮件和派发保留未知结果保护，不能声称外部副作用绝对只执行一次。

PG 不可用时启动失败或运行请求明确失败，健康检查返回503；不创建 SQLite、不返回假空数据、不自动恢复旧存储。恢复连接后按原持久化状态和有效租约继续，超过租约期限的 Worker 必须重新启动获取新 owner。

## 备份与回退

正常运行后的 PG 备份使用 PostgreSQL 自带工具，并与同批报告/下载文件、私有配置一起保存。以下输出在宿主机 `/data`，不经过 Web 容器：

```bash
mkdir -p /data/cutting-p5-backup
chmod 700 /data/cutting-p5-backup
umask 077
backup_dir="$(mktemp -d /data/cutting-p5-backup/pg-XXXXXXXX)"
docker compose exec -T postgres sh -c 'pg_dump -U postgres -d "$POSTGRES_DB" -Fc' \
  > "$backup_dir/state.dump"
docker compose exec -T postgres pg_restore -l \
  < "$backup_dir/state.dump" > "$backup_dir/state.contents"
```

确认两个命令成功后再将备份标记为完成；每次使用新的备份文件名，保留上一份可恢复备份。`pg_restore -l` 只校验归档可读，恢复演练应在空的隔离数据库执行，不能覆盖运行库。需要一致的数据库/实体文件恢复点时，先停止本项目 Web/Worker，再同时备份状态库与文件卷。

- 切换前保持 SQLite 一致性备份、原文件卷、配置、旧镜像可用；PG 卷也单独备份。
- 若尚未放开新版本写入，停止新 Web/Worker，恢复旧程序/镜像/配置并连接原 SQLite/文件卷，即可回到切换前状态。
- 如果新版本已经写入新用户、确认、告警或邮件状态，不能直接回旧 SQLite；它不包含新数据。先停止写入、备份 PG 和文件，按实际差异制定恢复方案。没有自动反向同步或双写。
- 不删除 postgres_data 或原 SQLite 来“试着重装”；不对整个 Docker 执行清空操作。

P6/P7 再处理 Scheduler 拆分和多 Worker/多实例部署；P5 仍限定同机一个 Web、一个 Worker，保留 SQL/LLM/PDF 本机文件容量锁。


## 隔离验收复现

测试仅针对一次性本地PG18.6实例，不能指向生产。以 `scripts/postgres/10-roles.sql` 初始化测试角色，测试数据库为 `p5_test`；本地端口55435，测试管理员postgres密码为 `p5-isolated-test-only`，cutting_migrator为 `p5-migrator-test-only`，cutting_app为 `p5-app-test-only`。这些是隔离夹具凭据，不是部署密码。测试用管理员创建/删除随机 `cutting_p5_*`、`cutting_p4_*` 数据库；业务连接仍用最小权限账号。

```bash
P5_TEST_POSTGRES=1 PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/test_postgres_runtime.py -o addopts='' -q
P4_TEST_POSTGRES=1 P5_TEST_POSTGRES=1 P4_TEST_POSTGRES_PORT=55435 \
  P4_TEST_POSTGRES_DATABASE=p5_test P4_TEST_POSTGRES_PASSWORD=p5-isolated-test-only \
  PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_postgres_migration.py -o addopts='' -q
```

未显式启用时上述PG测试跳过，不回退SQLite。实际本轮使用独立Python3.12测试环境执行，验收结果见 [架构记录](../project/architecture.md#p5postgresql-唯一状态库2026-11-04)。


## P7 Consumer协议切换

P7的003迁移拒绝任何仍持有效租约的旧/新Consumer。旧SQLite仍先完成本说明的最新数据导入与verify，再显式使用cutting_migrator运行 `python scripts/migrate_sqlite_to_postgres.py prepare-consumers`。P6 PostgreSQL现场同样先停所有Consumer、备份并确认退出，执行schema再执行prepare-consumers。准备步骤恢复执行态、保留业务ID/重试和完成记录，旧无来源任务标blocked，不猜测来源；移除旧全局worker租约后才允许启动新实例。该步骤会有意改变执行状态，应在逐表导入校验之后执行，不能由普通启动代办。完整单/多副本、连接预算及回退见[四角色与多Consumer部署](WEB_WORKER进程隔离升级说明.md)。不允许旧Worker与实例租约协议混跑；回退P6必须停止新实例并恢复匹配备份，不能只更换镜像或删除版本标记。

## 已确认的旧库结构兼容（2026-11-05）

部分历史库通过ALTER新增report_analyses.data_version，声明为可空；只有这一声明差异且实际值均非NULL时，导入器接受原值。真实NULL仍拒绝，不猜填版本、不改写旧分析。旧版workshop_source_correction_audits在功能移除时保留，匹配原8列/主键及两索引定义时完整导入；未知字段、约束或其他多余表仍拒绝。

追加004_legacy_workshop_audits，001–003不改写。PG增加历史表及两个索引，运行角色仅SELECT，不恢复源更正API/仓储写入；保留原始audit_id、report_id、task_id、payload、原因、操作人与时间，无新增外键猜测历史关联。PG共42表/102索引；源库仍为38张正式状态表，可额外包含这1张退役历史表。逐表计数/字段摘要及重复verify覆盖归档，不允许目标存在源库没有的审计记录。

20261104-v1在check阶段因上述差异暂停的现场，保留已有完整备份及PG卷，补入配套源码和004迁移后使用scripts/linux/resume_sqlite_upgrade.sh，从schema/check/migrate/verify继续，不再重跑首次脚本。默认备份目录为现场已完成的/data/cutting-before-upgrade-SR9p3Ufy，也可用第一个参数显式指定。脚本拒绝活动应用，先单独备份当前PG再升级/导入，verify成功后才prepare-consumers并启动；不重建或清空卷。真实NULL/未知结构应继续停下核对，不能通过放宽校验掩盖。
