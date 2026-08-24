# Docker Excel 输入目录

只有在 `APP_DATA_SOURCE_MODE=excel` 时需要把数据文件放到这里：

```text
docker/input/TASK.xlsx
```

Excel 文件已被 Git 和 Docker 构建上下文忽略，不会写入代码仓库或容器镜像。SQL Server 模式不需要该文件。
