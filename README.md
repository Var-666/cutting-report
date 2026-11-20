# 切割生产智能产报系统

面向内网生产现场的报表与任务管理系统，汇总切割、焊接和 PDM 数据，为管理人员、车间和工人提供统一的查询、分析与协作入口。

## 主要功能

- **生产报表**：领导、车间、设备及焊接统计，支持日期筛选、图表和明细查询。
- **车间任务**：工人任务确认、工时归属及操作记录。
- **CSS资料统计**：设备资料与详设图纸的进度、完成率和分组统计。
- **智能分析与告警**：生产数据分析、设备告警和异常排查。
- **报表交付**：HTML/PDF 导出、定时报表及邮件推送。
- **文件派发与系统管理**：切割指令文件查询和派发，账号权限、数据源、缓存及运行状态管理。

## 快速启动

推荐使用 Docker Engine 与 Compose v2。以下步骤用于**全新安装**，首次构建需要联网；已有部署请按 [Docker 更新说明](README-DOCKER.md#更新已有部署) 操作。

1. 在项目根目录复制配置模板；已有 `.env` 时保留原文件。

   ```bash
   cp .env.example .env
   ```

2. 编辑 `.env`，为 `POSTGRES_PASSWORD`、`POSTGRES_MIGRATOR_PASSWORD`、`POSTGRES_BOOTSTRAP_PASSWORD` 设置三个不同的非空密码。模板默认使用演示数据，接入生产库时参阅 [配置参考](docs/deployment/配置参考.md)。

3. 构建镜像、初始化状态库并启动服务。

   ```bash
   docker compose build app
   docker compose up -d --wait postgres
   docker compose run --rm --no-deps migrate python scripts/manage_postgres.py schema
   docker compose up -d --wait --wait-timeout 180 app scheduler worker automation
   docker compose ps
   ```

访问 **[http://127.0.0.1:8088](http://127.0.0.1:8088)**，首次打开时创建本地管理员。

应用由 Web、Scheduler、Consumer 和 Automation 四类进程组成，默认运行一个 Consumer。PostgreSQL 是必需的应用状态库，演示模式也需要它。内网离线安装参阅 [离线部署说明](docs/deployment/OFFLINE_DOCKER部署说明.md)。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Soybean Admin、Naive UI、ECharts |
| 后端 | Python、FastAPI、Pydantic、Jinja2 |
| 应用状态 | PostgreSQL |
| 外部数据源 | SQL Server、MySQL |
| 报表与分析 | HTML/PDF、Playwright/Chromium、可选 OpenAI-Compatible 模型 |

## 项目结构

```text
backend/app/     API、业务服务、数据访问与后台任务
backend/tests/   后端测试
frontend/src/    页面、组件、接口与主题
frontend/tests/  前端测试
scripts/        部署、迁移、诊断与打包工具
docs/           开发、业务与部署文档
```

## 开发与文档

开发环境使用 Python 3.12、uv 和 Node.js 22.18+、pnpm；具体版本以项目版本文件和锁文件为准。安装依赖、本地启动、测试与构建步骤见 [开发指南](docs/project/development.md)。

| 文档 | 用途 |
| --- | --- |
| [完整文档目录](docs/README.md) | 按场景查找使用、开发和运维资料 |
| [功能与接口参考](docs/project/功能与接口参考.md) | 业务功能、统计口径与接口说明 |
| [配置参考](docs/deployment/配置参考.md) | 数据源、模型、邮件及运行参数 |
| [Docker 部署与更新](README-DOCKER.md) | 联网安装、更新和运行检查 |
| [麒麟原生部署](docs/deployment/KYLIN原生部署说明.md) | 非 Docker 环境安装与启动 |
| [系统架构](docs/project/architecture.md) · [开发规范](AGENTS.md) | 模块职责、开发边界与测试要求 |

生产数据源按只读方式接入，应用状态由 PostgreSQL 保存。配置、凭据及 `data/`、`reports/`、`logs/` 等运行数据不提交到 Git。更新已有系统前，请按部署文档备份数据库和文件卷。
