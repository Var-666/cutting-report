# 脚本目录

Linux/Docker 部署见 [Docker 说明](../README-DOCKER.md)，麒麟原生部署见 [安装说明](../docs/deployment/KYLIN原生部署说明.md)。原生环境分别运行启动脚本及同一脚本的 `worker` 参数，将两个进程注册为独立服务；升级、健康和回滚见 [Web/Worker 说明](../docs/deployment/WEB_WORKER进程隔离升级说明.md)。

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
    ├── start_kylin.sh            启动 Web；worker 参数启动后台
    └── start_offline_docker.sh   使用离线镜像和当前程序启动
```

Shell 脚本会从自身目录定位项目根目录，部署时保留相对目录结构。常规 Docker 构建直接在项目根目录执行 `docker compose up -d --build`。
