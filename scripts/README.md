# 脚本目录

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
