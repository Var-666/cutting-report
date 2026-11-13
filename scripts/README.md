# 维护脚本

脚本通过自身位置定位项目根目录，部署时保留相对路径。操作前按[文档入口](../docs/README.md)选择对应流程，当前只提供PG维护与更新，不包含SQLite升级/导入入口。

| 脚本 | 用途与边界 |
| --- | --- |
| `build_docker_package.py` | 构建前端并生成带SHA-256清单的源码ZIP，不生成镜像或收录运行数据 |
| `linux/start_offline_docker.sh` | 校验配套镜像与现有PG结构，启动四角色；不安装Schema或迁移历史 |
| `linux/update_offline_docker.sh` | 已完成PG/Consumer切换后的普通更新；预检、停四角色、备份、追加迁移及启动 |
| `linux/install_kylin.sh`、`start_kylin.sh` | 麒麟原生安装及按web/scheduler/worker/automation角色启动 |
| `migrate_production_lines.py` | 一次性转换旧连接/设备配置，保护密码、原子写入；发现旧SQLite库拒绝操作，不连接生产库 |
| `manage_postgres.py` | PG结构安装、状态校验及停机Consumer协议维护，不支持SQLite导入 |
| `reconcile_cutting_completion_dates.py` | 按真实结束日校正历史车间日报，默认预览；`--line`限定切割产线，`--apply`停机提交并保留确认及审计。操作见[离线部署](../docs/deployment/OFFLINE_DOCKER部署说明.md#切割任务改按结束日期统计) |
| `check_energy.py` | 切割能管只读诊断，必须显式指定`--device-id` |
| `check_welding.py` | 焊接源库只读核验，必须显式指定`--line-id` |
| `prepare_alarm_monitor.py` | 告警预览与显式维护；`--apply`不是迁移步骤，不能用于跳过归属校验 |
| `export_api_contracts.py` | 不启动Runtime、不连接数据源地导出OpenAPI，前端通过现有生成命令使用 |

## 生成部署包

先在`frontend/`执行`pnpm install --frozen-lockfile`，再从项目根目录运行：

```bash
python3 scripts/build_docker_package.py --name cutting-report-linux-docker-YYYYMMDD
```

脚本按项目固定pnpm构建并收录当前工作区，已有同名包不覆盖。根目录`.env.deploy`存在时会以`.env`进入包，可能包含密码/密钥，不提交Git或公开分发；工作区`.env`不会自动收录。

普通更新命令见[离线部署](../docs/deployment/OFFLINE_DOCKER部署说明.md)。PG账号、Schema和备份见[迁移说明](../docs/deployment/PostgreSQL状态库迁移.md)，多Consumer与回退见[后台进程说明](../docs/deployment/WEB_WORKER进程隔离升级说明.md)。
