# 部署包目录

本目录只用于本地保存生成后的ZIP部署包，ZIP文件由 `.gitignore` 排除，不提交Git。

```text
packages/
├── README.md
├── cutting-report-windows-docker-20260831-files-v2.zip  当前文件下载更新版
├── cutting-report-windows-docker-20260831.zip       上一版（保留）
├── cutting-report-windows-optimized-20260825-v2.zip  之前版本（保留）
└── archive/                                       旧版本与重复副本
```

约定：

- 新生成且通过测试的部署包放在 `packages/` 根目录；
- 旧版本可保留原路径，确认无需兼容原下载链接后再移入 `packages/archive/`；
- 文件名包含平台、用途和日期；
- 不在项目根目录散放ZIP；
- 不把 `.env`、运行数据库、日志或生产资料写入部署包。

最新包包含能耗日/月报的表格、汇总与图表，以及 `START_DOCKER.bat`、`README-DOCKER.md`。
文件下载更新版增加文档/版本命名、本地保存、下拉选择需要下载的文件和已下载文件单选，不包含内容预览。
按 `scripts/build_docker_package.py` 的白名单从当前工作区生成，包含尚未提交的新功能文件。
这是与之前同类的源码构建包，不含镜像 TAR，首次 Docker 构建需要联网。
