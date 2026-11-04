# 脚本目录

Linux/Docker 部署见 [Docker 说明](../README-DOCKER.md)，麒麟原生部署见 [安装说明](../docs/deployment/KYLIN原生部署说明.md)。原生环境分别运行启动脚本及同一脚本的 `scheduler`、`worker`、`automation` 参数，将四个应用进程注册为独立服务；升级、健康和回滚见 [后台进程说明](../docs/deployment/WEB_WORKER进程隔离升级说明.md)。

## 生成源码部署包

```bash
python3 scripts/build_docker_package.py --name cutting-report-linux-docker-YYYYMMDD
```

先在 frontend/ 执行 `pnpm install --frozen-lockfile`。脚本使用项目固定的 pnpm 构建前端，再按清单收录当前工作区文件，生成含 SHA-256 清单的 ZIP；不生成离线镜像，也不包含运行数据库和日志。

根目录存在 `.env.deploy` 时会以 `.env` 名称写入部署包。该文件不提交 Git；含部署配置的 ZIP 可能包含密码和密钥，需按敏感文件保管。已有部署包不覆盖。

```text
scripts/
├── build_docker_package.py       构建前端并打包源码
├── check_energy.py               只读能管联调
├── prepare_alarm_monitor.py      告警监控配置检查与维护
└── linux/
    ├── install_kylin.sh          创建虚拟环境并安装依赖
    ├── start_kylin.sh            启动 Web；scheduler/worker/automation 参数启动各后台角色
    └── start_offline_docker.sh   使用离线镜像和当前程序启动
```

Shell 脚本会从自身目录定位项目根目录，部署时保留相对目录结构。Docker 首次启动前显式准备 PostgreSQL Schema；从 SQLite 升级还须停机、离线导入及校验，见 [切换说明](../docs/deployment/PostgreSQL状态库迁移.md)。


P1 旧部署先停 Web/Worker，运行 `python scripts/migrate_production_lines.py`，再启动新版本。该工具只读旧连接/设备配置，保护凭据并原子写入统一配置；重复执行只验证，不回退旧机制。新增同类型实例配置与015告警迁移边界见 [配置参考](../docs/deployment/配置参考.md#p1-升级与新增实例)。焊接核验脚本必须提供 `--line-id`；切割诊断的 `--device-id` 继续表示原稳定产线键。

P7 Consumer默认1个，同机扩缩容使用 `docker compose up -d --no-deps --scale worker=2 worker`（或3/1）；持久化数量设置CONSUMER_REPLICAS。原生每个服务显式设置不同CONSUMER_INSTANCE_ID。旧SQLite先完成P5最新迁移/verify；旧P6先停Consumer并追加003，再执行prepare-consumers，不能直接混跑两种租约协议。实例探针、容量预算和回退见[部署说明](../docs/deployment/WEB_WORKER进程隔离升级说明.md)。
