# 麒麟离线 Docker 部署与更新

离线镜像包含 Python、Microsoft ODBC Driver 18、Playwright Chromium、中文字体和应用依赖。麒麟服务器只在首次部署或依赖变化时导入镜像；普通功能更新只替换程序文件。

## 首次部署

把镜像包和程序 ZIP 放在同一目录：

```bash
docker load -i cutting-report-image-amd64-YYYYMMDD-vN.tar.gz
unzip cutting-report-kylin-offline-docker-YYYYMMDD-vN.zip
cd cutting-report-kylin-offline-docker-YYYYMMDD-vN
chmod +x scripts/linux/*.sh
./scripts/linux/start_offline_docker.sh
```

镜像只提供稳定运行环境。`compose.offline.yaml` 会将部署目录中的 `backend`、`frontend/dist` 和 `vendor` 只读挂载到容器，因此页面和后端代码以当前程序 ZIP 为准。

## 普通功能更新

普通更新不需要重新导入镜像：

```bash
unzip cutting-report-kylin-offline-docker-新版本.zip
cp 旧版本目录/.env 新版本目录/.env
cd 新版本目录
chmod +x scripts/linux/*.sh
./scripts/linux/start_offline_docker.sh
```

Compose 项目名固定为 `cutting-report`，所以管理员、确认记录、已下载文件和生成报表仍使用原有命名卷。不要执行 `docker compose down -v`。

如需回滚，进入旧版本目录再次运行 `./scripts/linux/start_offline_docker.sh`。

## 需要更新镜像的情况

以下文件或运行环境发生变化时才需要新的离线镜像：

- `backend/requirements.txt`；
- `backend/requirements-sqlserver.txt`；
- `Dockerfile` 中的 Python、ODBC、Chromium或系统包；
- 容器启动方式或基础镜像。
