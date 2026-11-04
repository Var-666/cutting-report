# 项目文档目录

本目录只将经过检查的开发、配置和运维说明纳入版本控制。内部数据库资料、厂商 PDF/Excel、操作手册及本机环境文件继续留在本地，不属于共享文档。

- [快速启动与数据源接入](../README.md)
- [开发约定与当前架构](project/README.md)
- [配置参考](deployment/配置参考.md)
- [功能与接口参考](project/功能与接口参考.md)
- [Docker 部署](../README-DOCKER.md)
- [离线 Docker 部署](deployment/OFFLINE_DOCKER部署说明.md)
- [麒麟原生部署](deployment/KYLIN原生部署说明.md)
- [Web/Worker 升级与回滚](deployment/WEB_WORKER进程隔离升级说明.md)
- [PostgreSQL 仓储与事务](design/PostgreSQL仓储与事务设计.md)
- [SQLite 离线历史参考](design/SQLite仓储与事务设计.md)
- [API 契约与迁移](design/API契约与迁移约定.md)

新增共享文档时同步调整 `.gitignore` 白名单并检查链接和敏感信息，不提交原始连接凭据或生产数据。
