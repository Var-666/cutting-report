# 脚本目录

Windows Docker 部署优先使用根目录 `START_DOCKER.bat`，操作说明见 `README-DOCKER.md`。
打包当前程序（包含未提交的新功能文件，不包含密码和运行数据）：

```sh
python scripts/build_docker_package.py --name cutting-report-windows-docker-YYYYMMDD
```

脚本会先构建前端，再生成带 SHA-256 文件清单的源码 ZIP；不生成离线 Docker 镜像。

```text
scripts/
└── windows/
    ├── install_windows.bat  创建虚拟环境并安装依赖
    └── start_windows.bat    使用Uvicorn启动本地服务
```

两个批处理脚本会自动从自身目录返回项目根目录，因此必须保留当前相对目录结构。

Docker部署不需要运行这些脚本，直接在项目根目录使用：

```cmd
docker compose up -d --build
```
