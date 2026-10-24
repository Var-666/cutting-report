# 项目开发指南

## 项目用途

本项目是面向内网的切割生产智能产报系统，提供领导、车间和设备报表，工人任务确认，智能分析与告警，报表导出与自动发送，以及切割指令文件管理。

## 技术栈

- 前端：Vue 3、Vue Router、Vite、ECharts。
- 后端：Python、FastAPI、Pydantic、Uvicorn、Jinja2。
- 数据：SQL Server 作为生产数据源，SQLite 保存系统元数据、快照和操作记录。
- 报表：单文件 HTML、ECharts、Playwright/Chromium PDF 转换，可选 OpenAI-Compatible LLM 分析。
- 测试：Pytest 和 Node.js 内置测试运行器。

## 核心目录

- `backend/app/api/`：HTTP 路由、请求校验和权限边界。
- `backend/app/services/`：业务流程、统计、外部集成和报表生成。
- `backend/app/database/`：SQL Server 和 SQLite 仓储。
- `backend/app/workers/`：报表、告警、缓存刷新和清理后台任务。
- `backend/tests/`：后端回归测试。
- `frontend/src/views/`：页面；`components/` 为可复用组件；`layouts/` 为角色/功能布局。
- `frontend/src/api/`：前端 API、缓存、日期和导出边界。
- `frontend/tests/`：路由、页面契约、缓存和导出测试。
- `docs/`：需求、设计、部署、数据库和外部接口资料。
- `data/`、`reports/`、`logs/`、`tmp/`：运行时数据，不属于源码。

## 主要架构约定

- API 层保持精简；业务规则放在 service，持久化放在 repository/database 层。
- 生产 SQL Server、HR 库、PDM 库和能管平台均按外部系统处理。除非需求和设计明确授权，不对其执行写入。
- SQLite 是应用自有状态库；多仓储原子操作应遵循 `docs/design/SQLite仓储与事务设计.md`。
- 生产式部署由 FastAPI 同源提供构建后的前端和 API。后台工作线程依赖单个 Uvicorn 进程，不得随意改为多 worker。
- 管理员与工人路由、菜单和后端 API 权限必须一致；不能仅依赖前端隐藏实现授权。
- 页面查询、HTML/PDF 导出和智能分析应共用同一统计口径、时区和区间定义。
- 管理页保存的运行参数写入 `data/settings.env`，密码不回显；数据库页全部配置及自动化/SMTP 参数按新操作热加载，其余启动参数修改后重启生效。

## 编码和验证规范

- 每次任务先检查 `git status`/`git diff`，再读取本次直接相关的文档和代码。
- 优先最小范围修改；不重构、格式化或修复任务外代码。
- 只实现用户明确说明的功能；不得基于未经确认的业务假设自行增加页面入口、操作流程或管理功能。未明确说明的新增需求或业务规则，先澄清再实现。
- 保留现有风格：Python 使用类型标注和 Pydantic 边界校验；Vue 使用 Composition API；JavaScript 不强制分号。
- 公共 API、数据结构、认证、统计口径或背景工作机制改动前，先用 `rg` 检查调用方和对应测试。
- 后端验证：`./.venv/bin/python -m pytest backend/tests`。
- 前端验证：在 `frontend/` 执行 `npm test` 和 `npm run build`。
- 功能修改应补充相同层级的回归测试；涉及 UI 时同时检查窄屏和空数据/加载/失败状态。

## 不应随意修改

- 不要提交 `.env`、`data/`、`reports/`、`logs/`、`tmp/`、Excel/PDF 原始资料或任何生产密钥、Token、Cookie。
- 不要手工编辑 `frontend/dist/` 或运行时生成数据。`vendor/echarts.min.js` 由前端构建脚本同步。
- 不要在未核对业务和设备映射时改变报表统计范围、单位、日期归属、能耗仪表组或告警设备范围。
- 不要让浏览器获得数据库密码、LLM 密钥、邮箱密码或外部系统授权信息。
- 工作区中已有的未提交修改默认属于用户或其他会话；不要覆盖、回退或夹带提交。

## 文档维护

- 开始任务时只读取 `docs/` 中与任务直接相关的文档。
- 只记录对后续开发有用的结果：架构、UI 约定、性能优化、技术决策和当前进度分别写入 `docs/project/` 中的对应文档。
- `docs/project/progress.md` 保持简洁，只保留“当前状态、最近完成、当前问题、下一步”；不记录聊天过程。
