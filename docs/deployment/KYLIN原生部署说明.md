# 麒麟 V10 原生部署说明

进程隔离版本需独立启动 Web 与 Worker；首次升级、配置传播和回滚以 [Web/Worker 升级说明](WEB_WORKER进程隔离升级说明.md) 为准。原生启动脚本使用 `worker` 参数启动后台，分别注册为系统服务。

本方式不使用 Docker。Docker Hub 无法拉取 `hello-world` 不影响原生部署，但服务器仍需能够安装 Python 依赖和 Playwright Chromium。

## 服务器准备

- x86_64 麒麟 Linux Advanced Server V10；
- Python 3.11 或 3.12，以及对应的 `venv`；
- `unixODBC`、`unixODBC-devel` 和 Microsoft ODBC Driver 18 for SQL Server；
- 到生产库、工人库、能管平台和文件接口的内网连通性；
- 对测试终端开放 `.env` 中 `APP_PORT` 指定的 TCP 端口。

项目包已经包含构建后的前端文件，服务器不需要安装 Node.js。

## 安装

将 ZIP 上传到服务器并解压，例如放到 `/opt/cutting-report`，然后运行：

```bash
cd /opt/cutting-report
chmod +x scripts/linux/*.sh
./scripts/linux/install_kylin.sh
```

如果 Python 命令名为 `python3.11`：

```bash
PYTHON_BIN=python3.11 ./scripts/linux/install_kylin.sh
```

安装脚本会保留包内已有的 `.env`。部署包包含正式配置时应按敏感文件保管，并将项目目录限制为管理员可读。

## 启动和验证

```bash
./scripts/linux/start_kylin.sh
```

根据当前部署配置，访问：

```text
http://服务器IP:8000
http://服务器IP:8000/api/v1/health
```

若应用能启动但 PDF 导出失败，通常是 Chromium 所需的系统动态库尚未安装。先保留错误输出，再根据缺少的 `.so` 安装对应的麒麟软件包。
