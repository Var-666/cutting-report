# PostgreSQL结构维护、备份与恢复

当前版本仅支持PostgreSQL应用状态库，不包含旧SQLite升级/导入能力。尚未迁移的旧部署须使用保留的历史版本和其工具先完成迁移，再更新到当前程序；不要删除旧库或按新安装覆盖已有数据。

## 连接与账号

状态库与生产SQL Server/MySQL分开，不登记在`DATABASE_SOURCES_JSON`。运行四角色只用`cutting_app`，维护工具显式使用`cutting_migrator`；初始化管理员只用于首次建库。三个密码不同，运行账号无DDL/SUPERUSER/CREATEDB/CREATEROLE权限。

配置使用`POSTGRES_HOST/PORT/DATABASE/USER/PASSWORD`及已有池参数。`data/settings.env`优先于进程环境和`.env`，PG配置为冷配置，修改后重建四角色。已有PG卷不会因修改`.env`而自动更改数据库账号密码。

默认Compose项目`cutting-report`保留`postgres_data`及data/reports/logs卷；PostgreSQL 18挂载到`/var/lib/postgresql`且不公开5432端口。每角色独立有界池，同机共享文件卷；容量与探针见[后台进程说明](WEB_WORKER进程隔离升级说明.md)。

## 初始化与追加Schema

正式维护入口为`scripts/manage_postgres.py`，只提供`schema/status/prepare-consumers`。`schema`安装缺少的编号迁移并授予原运行权限；`status`校验已发布版本、校验和与实际结构。普通应用启动只校验，不自动安装Schema。

在完整程序目录操作，先准备匹配镜像与配置；全新安装可执行下列步骤，已有部署按[普通更新](OFFLINE_DOCKER部署说明.md#已有postgresql更新)停机备份后处理：

```bash
docker compose -f compose.yaml -f compose.offline.yaml up -d --no-build --pull never postgres
docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  python scripts/manage_postgres.py schema
docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  python scripts/manage_postgres.py status
```

已有`001_initial`至`006_dispatch_completion`及指纹保持不变，只能追加新迁移，不删除版本行、修改已发布脚本或让请求临时建表。PG中原有历史导入元数据表及退役审计继续保留，不影响正常业务，也不代表仍运行SQLite。

原生维护使用相同脚本；仅在维护终端配置`cutting_migrator`及对应密码，不把密码放命令参数或共享脚本。维护错误不打印驱动凭据，不将失败标记为成功。

## Consumer协议维护

仅旧PG全局Worker协议切换时使用`prepare-consumers`，不是普通更新步骤：先停止全部应用并禁用旧Consumer自动重启，备份PG/配置/文件，确认旧进程停止及租约释放/失效，再由维护账号执行：

```bash
docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate \
  python scripts/manage_postgres.py prepare-consumers
```

操作拒绝有效Consumer租约；恢复中断后台/报表/能耗执行态，保留业务ID与重试计数，无可信来源任务标blocked，移除旧全局worker租约。不猜来源、不恢复SMTP或派发未知结果为可重试。正常启动不代办，详细步骤和回退见[后台说明](WEB_WORKER进程隔离升级说明.md#升级门槛与顺序)。

## 备份与回退

需要一致恢复点时，先停本项目四个应用角色，保存同批配置、完整data/reports/logs文件卷及旧程序/镜像，不操作其他项目：

```bash
docker compose stop app scheduler worker automation
mkdir -p /data/cutting-pg-backup
chmod 700 /data/cutting-pg-backup
umask 077
backup_dir="$(mktemp -d /data/cutting-pg-backup/pg-XXXXXXXX)"
docker compose exec -T postgres sh -c 'pg_dump -U postgres -d "$POSTGRES_DB" -Fc' \
  > "$backup_dir/state.dump"
docker compose exec -T postgres pg_restore -l \
  < "$backup_dir/state.dump" > "$backup_dir/state.contents"
```

两个命令均成功才能把PG备份标记完成；每次使用新目录，另存配置和实体文件，不覆盖上一份备份。`pg_restore -l`只证明归档可读，恢复演练在空的隔离库进行，不覆盖运行库。

恢复前停止全部角色并另存当前PG/文件，使用同批备份与匹配版本程序/镜像。已经产生新确认、派发、审计或邮件状态时，不能直接恢复旧库或只换源码；需保全当前状态并制定差异恢复方案，避免丢记录和重复外部副作用。

不得`down -v`、清空原卷、删除迁移标记或手改租约“试着重装”。保留的历史SQLite文件不由当前程序读取、导入或删除，不能当作PG当前业务备份。

## 验证与故障边界

真实PG回归复用`backend/tests/postgres_fixtures.py`，仅在显式启用的隔离PG18实例中创建/删除随机测试库。维护CLI、结构与事务回归位于`test_postgres_maintenance.py`、`test_postgres_migration.py`、`test_postgres_runtime.py`；未启用而跳过不算通过。

PG不可用时启动或请求明确失败，不返回假空数据、不回退SQLite。租约、提交围栏、连接与队列验证见[容量验证](PostgreSQL并发与队列容量验证.md)，本地结果不代替生产连接及负载验收。
