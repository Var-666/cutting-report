# 项目文档

按使用场景查阅；每项规则只维护一处，其他文档引用，不复制整段内容。

## 开发与业务

| 文档 | 内容 |
| --- | --- |
| [项目说明](../README.md) | 项目用途、本地开发、依赖与构建入口 |
| [开发规范](../AGENTS.md) | 开发边界、测试策略、表格与主题规范 |
| [系统架构](project/architecture.md) | 当前模块、Runtime、数据源与后台角色职责 |
| [功能与接口](project/功能与接口参考.md) | 统计口径、确认、派发、回调、分析及导出 |
| [报表缓存与快照](project/multi-line-report-data.md) | 多产线复用、快照身份、新鲜度与刷新 |
| [焊接接入](project/welding-integration.md) | 焊接配置、指标口径和专属接口 |
| [当前进度](project/progress.md) | 当前状态、最近完成、问题与下一步 |
| [API契约与迁移](design/API契约与迁移约定.md) | 类型生成、Schema与车间仓储边界 |
| [PostgreSQL事务设计](design/PostgreSQL仓储与事务设计.md) | 连接、事务、并发和提交围栏 |

## 配置与部署

| 文档 | 内容 |
| --- | --- |
| [配置参考](deployment/配置参考.md) | 配置来源、热加载、数据源与运行参数 |
| [Docker部署](../README-DOCKER.md) | 联网构建和Docker基础操作 |
| [离线Docker部署与更新](deployment/OFFLINE_DOCKER部署说明.md) | 新安装、已有PG更新、镜像与故障核对 |
| [麒麟原生部署](deployment/KYLIN原生部署说明.md) | 非Docker安装和启动 |
| [后台进程与扩缩容](deployment/WEB_WORKER进程隔离升级说明.md) | 四角色、Consumer、健康与回退 |
| [PostgreSQL迁移与备份](deployment/PostgreSQL状态库迁移.md) | 账号、Schema、导入校验及恢复 |
| [容量验证](deployment/PostgreSQL并发与队列容量验证.md) | 隔离测试证据与现场容量边界 |
| [维护脚本](../scripts/README.md) | 打包、诊断与升级脚本选择 |

## 维护约定

- 修改功能时更新对应现行说明，不逐次新增日期命名的更新文档。
- 进度只保留当前状态与近期结果；已被替代的步骤、测试流水账和临时排查记录删除，源码历史通过Git查阅。
- 迁移、备份、回退条件及仍有效的统计/权限约束必须保留；文档不把本地验证当作现场验收。
- 新共享文档同步更新本入口、`.gitignore`白名单及有实际需要的打包清单。内部数据库原稿、厂商PDF/Excel和本机资料继续忽略，不随此次整理删除或发布。
