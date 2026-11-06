# 麒麟离线Docker部署与更新

适用于同机x86_64/amd64 Docker环境。应用采用Web、Scheduler、Consumer、Automation四角色，共用PostgreSQL及本地文件卷；默认1个Consumer。其他架构须另备匹配镜像。当前程序只支持PG，未迁移的SQLite部署须先用保留的历史版本工具完成迁移。

## 先选择操作

| 现场状态 | 操作入口 |
| --- | --- |
| 全新安装，无历史状态数据 | 本文“全新安装” |
| 已完成PostgreSQL与实例Consumer切换 | 本文“已有PostgreSQL更新” |
| 旧PG全局Worker协议 | [后台进程升级](WEB_WORKER进程隔离升级说明.md#升级门槛与顺序)，停机后显式准备Consumer |

## 程序包与镜像

离线程序包须包含`backend`、`frontend/dist`、`vendor`、Compose、脚本和`PACKAGE-MANIFEST.json`。`compose.offline.yaml`只读挂载当前程序文件，镜像提供Python、ODBC、Chromium、中文字体及依赖。应用镜像使用`cutting-report:local`，PG标签以随包Compose为准；镜像禁止联网拉取。

更新前核对包的SHA-256；有配套校验文件时在上传目录执行`sha256sum -c <校验文件>`。`.env`及程序包可能含私有凭据，按内部部署资料保管。

`backend/pyproject.toml`、`uv.lock`、`.python-version`必须与应用镜像指纹一致。锁输入、镜像基础依赖或架构变化时，先导入配套镜像；不跳过预检、不根据旧日期说明强行复用。单纯业务修改且指纹一致可复用现有镜像。

## 全新安装

准备完整程序包、匹配的应用及PG镜像和实际配置，解压进入程序目录。镜像包可以是包含两种镜像的一个归档，或分别提供；每个归档只导入一次：

```bash
docker load -i /实际路径/配套镜像.tar.gz
```

按[配置参考](配置参考.md)填写`.env`，PG三个账号密码必须不同，保留原Compose项目名及卷配置；不把PG登记为生产源。依次执行：

```bash
docker compose -f compose.yaml -f compose.offline.yaml up -d --no-build --pull never postgres
docker compose -f compose.yaml -f compose.offline.yaml run --rm --no-deps -T migrate   python scripts/manage_postgres.py schema
bash scripts/linux/start_offline_docker.sh
```

仅适用于真正的新安装；已有数据不得按空库重装。默认HTTP端口8088，绑定地址、防火墙和首次管理员初始化见[Docker说明](../../README-DOCKER.md)。

## 已有PostgreSQL更新

保留正在运行的旧部署目录、配置、镜像和卷；将新包解压到另一个目录。从新目录以root运行，参数填写**实际正在运行的旧目录**，不能填新目录：

```bash
bash scripts/linux/update_offline_docker.sh /实际旧部署目录 /data
```

第二参数是已存在的备份根目录，默认`/data`。脚本虽有旧目录默认值，日常更新应显式提供真实旧目录，避免备份错误版本配置。脚本已内置在程序包中，无需每次额外上传。

脚本在停服前检查项目、真实文件卷/PG卷、包内清单和新旧PG凭据；随后停止四个应用角色，备份PG、配置与文件，追加缺少的迁移，启动并检查健康。它不下载镜像、不重新导入SQLite、不重复`prepare-consumers`，不操作其他项目。

新旧PG连接及密码须保留现场值。已有PG卷不会因修改`.env`自动修改数据库角色密码；`data/settings.env`仍优先于包内配置，不用旧`.env`整份覆盖新配置。镜像指纹不匹配时先导入新配套镜像，再运行更新。

普通更新不能只替换backend、只重建Web或运行旧的start脚本代替备份和迁移。前端、各角色及部署清单使用同一份发布程序。

## 结果核对与失败处理

```bash
docker compose ps
docker compose logs --tail 100 app scheduler worker automation
docker compose exec scheduler python -m app.scheduler --health
docker compose exec --index 1 worker python -m app.worker --health
docker compose exec automation python -m app.automation_worker --health
curl --fail http://127.0.0.1:8088/api/v1/health
```

容器健康只证明应用/状态库与相应角色可用，生产SQL、HR、PDM、能管、魔盒、模型和邮件须按现场业务核验；不能把开关开启或`SELECT 1`成功等同于联调完成。

脚本失败会打印阶段和备份位置；新应用启动或健康失败会停止新角色。保留输出和备份，核对原因后处理，不删除卷、Schema版本或租约强行重试。升级后已有业务写入时，不直接恢复旧备份或只换旧源码；回退按[迁移说明](PostgreSQL状态库迁移.md#备份与回退)核对数据及外部副作用。

## 常用维护入口

- 配置来源、重启范围和魔盒完成回调：[配置参考](配置参考.md)；回调按唯一`taskIdentifier`关联真实已上传任务，完成机床可以不同于目标，旧拒收通知不会自动补发。
- Consumer扩缩容、实例探针及容量预算：[后台进程说明](WEB_WORKER进程隔离升级说明.md)。
- PG备份/导入/恢复：[状态库迁移](PostgreSQL状态库迁移.md)。
- 原生麒麟环境：[原生部署](KYLIN原生部署说明.md)。

不得执行`down -v`或清空原卷来解决更新失败；Docker存储路径沿用现场配置，程序更新不迁移Docker数据目录。
