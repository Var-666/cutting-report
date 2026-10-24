# 脚本目录

Windows Docker 部署优先使用根目录 `START_DOCKER.bat`，操作说明见 `README-DOCKER.md`。
麒麟 V10 可使用 `scripts/linux/` 下的脚本进行原生 Python 部署，不受 Docker Hub 拉取失败影响。
原生部署需要独立 Web/Worker：分别运行启动脚本及同一脚本的 `worker` 参数。两个进程应注册为不同系统服务；升级、健康和回滚见 `docs/deployment/WEB_WORKER进程隔离升级说明.md`。
打包当前程序（包含未提交的新功能文件，不包含运行数据）：

```sh
python scripts/build_docker_package.py --name cutting-report-windows-docker-YYYYMMDD
```

脚本会先构建前端，再生成带 SHA-256 文件清单的源码 ZIP；不生成离线 Docker 镜像。

根目录存在 `.env.deploy` 时，打包脚本会将它以 `.env` 的名称写入部署包。`.env.deploy` 已被 Git 忽略，不会进入代码仓库。由于部署包会含有数据库密码和接口密钥，应将压缩包按敏感文件保管。

```text
scripts/
├── windows/
│   ├── install_windows.bat  创建虚拟环境并安装依赖
│   └── start_windows.bat    使用 Uvicorn 启动本地服务
└── linux/
    ├── install_kylin.sh     在麒麟创建虚拟环境并安装依赖
    ├── start_kylin.sh       按 .env 的地址和端口启动服务
    └── start_offline_docker.sh  使用离线镜像和当前目录代码启动服务
```

两个批处理脚本会自动从自身目录返回项目根目录，因此必须保留当前相对目录结构。

Docker部署不需要运行这些脚本，直接在项目根目录使用：

```cmd
docker compose up -d --build
```
