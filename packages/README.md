# 部署包目录

本目录只用于本地保存生成后的ZIP部署包，ZIP文件由 `.gitignore` 排除，不提交Git。

```text
packages/
├── README.md
├── cutting-report-windows-optimized-20260825-v2.zip  当前推荐版本
└── archive/                                       旧版本与重复副本
```

约定：

- 新生成且通过测试的部署包放在 `packages/` 根目录；
- 被替换的旧版本移动到 `packages/archive/`；
- 文件名包含平台、用途和日期；
- 不在项目根目录散放ZIP；
- 不把 `.env`、运行数据库、日志或生产资料写入部署包。
