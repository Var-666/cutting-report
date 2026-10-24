# Docker 部署与更新

使用项目根目录的 `compose.yaml`，同时运行一个 Web（`app`）和一个独立后台进程（`worker`），共享原有 data/reports/logs 命名卷。需要 Docker Engine 与 Compose v2；首次源码构建需要联网。

## 首次部署

将源码包解压到固定目录，在项目根目录执行：

```bash
cp .env.example .env
```

已有 `.env` 时跳过复制，保留原配置。按 [配置参考](docs/deployment/配置参考.md) 填写生产数据源和只读连接；模板默认显式演示模式。

```bash
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
docker compose logs --tail 100 app worker
```

默认地址为 `http://127.0.0.1:8088`，首次打开创建本地管理员。如需内网访问，在 `.env` 设置 `DOCKER_BIND_ADDRESS=0.0.0.0`，由管理员限制防火墙来源；正式环境由反向代理提供 HTTPS。端口由 `DOCKER_PORT` 控制。

## 更新已有部署

首次从旧单进程或旧设备结构升级，先备份 `.env`、完整 data/reports 卷并停止旧服务，按 [Web/Worker 升级与回滚](docs/deployment/WEB_WORKER进程隔离升级说明.md) 和 [多设备迁移](docs/project/multi-device-isolation.md) 核对迁移条件。不能直接启动第二套实例或猜测历史数据归属。

保留原 Compose 项目名及卷配置，更新程序后构建两个服务：

```bash
docker compose up -d --build app worker
```

只修改宿主机 `.env` 时，重新创建两个容器以更新注入的环境变量：

```bash
docker compose up -d --force-recreate app worker
```

`docker compose restart` 不会重新注入环境变量。管理员保存的 `data/settings.env` 优先于进程环境和 `.env`；自动化/SMTP 通过下一轮操作热加载，数据库、模型、设备目录等需重启两个进程。在“系统设置 → 配置诊断”核对来源、保存目标与两个进程的实际状态。

停止服务可使用 `docker compose down`，保留命名卷；不要使用 `down -v`，它会删除运行数据。回滚条件与操作见双进程升级文档。

## 离线部署

在可联网机器上准备与目标服务器架构匹配的镜像。以下为 AMD64 示例：

```bash
docker buildx build --platform linux/amd64 -t cutting-report:local --load .
docker save -o cutting-report-image-amd64.tar cutting-report:local
```

将镜像与程序包带入内网。导入镜像后，按 [麒麟离线 Docker 部署](docs/deployment/OFFLINE_DOCKER部署说明.md) 使用 `compose.offline.yaml` 和 Linux 启动脚本；该方式挂载当前程序文件，普通功能更新不必重新导入运行环境镜像。依赖或基础镜像变化时需重新核对镜像。

## 健康检查与运行依赖

`app` 的健康状态表示 Web 与应用 SQLite 可用；Worker 是否就绪另由 `background_ready` 表示。Worker 具备独立心跳与健康探针：

```bash
docker compose ps
docker compose logs --tail 100 app worker
docker compose exec worker python -m app.worker --health
```

容器 healthy 不代表生产 SQL、HR、PDM、能管、模型或 SMTP 已联调成功，需核对容器到内网服务的连通性。不要分享 `.env` 或 `docker compose config` 的完整输出。

镜像已包含 ODBC Driver 18、Playwright Chromium 和中文字体，HTML/PDF 保存在 reports 卷；文件下载实体保存在 data 卷。凭据、设备仪表映射、采样规则和数据保留统一见 [配置参考](docs/deployment/配置参考.md)，页面操作与接口见 [功能与接口参考](docs/project/功能与接口参考.md)。
