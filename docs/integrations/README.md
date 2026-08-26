# 外部系统接口与关联说明

本目录保存PDM切割指令、FTP文件服务器和激光魔盒的受控参考资料。原始PDF、Excel默认不提交Git。

## 当前确认的关联关系

```text
BasicConfig_ProjectInfo.ID
  ← PDM_DesignDocumentInfo.ProjectInfoID

PDM_NCDocumentCatalog.ID
  ← PDM_DesignDocumentInfo.FileID

JDDR_PDM_NCDocumentVersion_Data.ID
  ← PDM_DesignDocumentInfo.VersionID

PDM_DesignDocumentInfo.ID
  → PDM_DocumentFileInfo.DocumentID
  → FilePath
```

魔盒与生产任务库的设计映射：

```text
魔盒 taskIdentifier
  → TASK.OUT_TASK_ID

魔盒上传成功返回的任务UUID
  → TASK.MAGIC_BOX_TASK_ID

上传文件名
  → TASK.FILE_NAME
```

当前导出的 `TASK.xlsx` 中 `OUT_TASK_ID`、`MAGIC_BOX_TASK_ID`、`DRAWING_NO` 和 `FILE_NAME` 尚未填充，因此上述映射需要在内网联调时验证真实写入链路。

## 仍需确认

1. FTP服务器地址、端口、根目录、账号和传输模式；
2. `.GEN` 文件能否直接由魔盒接收，或是否需要格式转换；
3. 魔盒任务领取/完成记录所在的接口或数据表；
4. 领取记录中的人员ID如何关联HR公司的工号；
5. PDM `Custom6` 推送成功后的正式状态文本；
6. 生产任务同步程序是否写入 `OUT_TASK_ID`、`MAGIC_BOX_TASK_ID` 和 `FILE_NAME`。
