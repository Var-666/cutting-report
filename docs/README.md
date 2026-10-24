# 项目文档目录

根目录只保留运行、构建和部署入口，项目说明材料统一放在本目录。

## 文档结构

```text
docs/
├── README.md                 文档总目录
├── project/                 当前架构、约定、决策和进度
├── deployment/              安装、部署、配置参数和运维说明
├── design/                  需求与系统设计
├── database/                数据库结构说明
└── integrations/            PDM、FTP、激光魔盒等外部系统资料
```

## 快速入口

### 使用与配置

- [快速上手](../README.md)
- [配置参考](deployment/配置参考.md)
- [功能与接口参考](project/功能与接口参考.md)
- [当前进度](project/progress.md) · [历史变更记录](project/changelog.md)

### 当前项目记忆

- [架构、后端、UI、优化、技术决策与进度](project/README.md)

### 部署

- [Docker 部署](../README-DOCKER.md)
- [Windows 内网部署](deployment/WINDOWS部署说明.md)
- [麒麟原生部署](deployment/KYLIN原生部署说明.md)
- [离线 Docker 部署](deployment/OFFLINE_DOCKER部署说明.md)
- [Web/Worker 升级与回滚](deployment/WEB_WORKER进程隔离升级说明.md)

### 需求与设计

- [需求说明书 V1.0](design/切割生产智能产报系统_需求说明书_V1.0.md)
- [概要设计说明书 V1.0](design/切割生产智能产报系统_概要设计说明书_V1.0.md)
- [SQLite仓储与事务设计](design/SQLite仓储与事务设计.md)

### 数据库

- [数据库设计说明 V2](database/数据库设计说明V2.pdf)

该PDF包含内网连接配置示例，属于内部受控资料。对外分享或推送远程仓库前必须完成脱敏，并更换其中出现过的Token、Secret和密码。

### 外部接口与原始资料

- `integrations/激光魔盒接口文档V2.1.pdf`
- `integrations/扬州-切割指令相关SQL.xlsx`
- [外部系统关联说明](integrations/README.md)

外部PDF和Excel属于本地参考资料，受项目 `.gitignore` 保护，默认不提交Git。其他开发环境需要单独取得授权副本。

## 目录约定

- 新增部署操作文档放入 `deployment/`；
- 新增需求、流程和架构设计放入 `design/`；
- 数据表、字段映射和数据库说明放入 `database/`；
- 厂商接口、PDM查询样例和联调记录放入 `integrations/`；
- 运行日志、报表输出和临时文件不得放入 `docs/`；
- 含账号、密码、Token、Secret或生产数据的原始资料不得直接提交Git。
