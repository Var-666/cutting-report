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

## 魔盒配置来源（用户确认）

用户提供的数据库截图确认：后续魔盒接入所需配置从生产侧数据库的
`dbo.SYS_CONFIG` 读取，以 `CONFIG_NAME` 为配置键、`CONFIG_VALUE` 为值，
`REMARK` 为说明。实际连接使用部署环境配置的只读数据库账号。
本节记录配置来源与接入约定，当前代码尚未实现该表的配置读取或真实魔盒调用。

| 配置键 | 截图中的用途 | 后续接入约定 |
| --- | --- | --- |
| `CUTTER_GATEWAY` | 切割机魔盒通信地址 | 用作魔盒 API 基础地址；具体路由以魔盒接口文档为准 |
| `CUTTER_TOKEN` | 魔盒通信 Token | 仅后台按接口认证规则使用，不返回前端或写入日志 |
| `CUTTER_SECRET` | 魔盒通信 Secret | 仅后台按接口认证规则使用，不返回前端或写入日志 |
| `CUTTER_PASSWORD` | 魔盒登录密码 | 仅在相应登录接口确有需要时使用，不返回前端或写入日志 |
| `CUTTER01_IP` / `CUTTER02_IP` | 1号 / 2号切割机 IP | 提供设备通信地址；与任务 `MACHINE_NO` 的对应关系仍需核对 |
| `CUTTER_WEB` | 魔盒前端页面地址 | 与 API 地址区分，不直接当作 API 基础地址 |
| `MODBUS_GATEWAY` | 流量计和电表的采集网关 | 仅说明采集网关地址，不包含协议端口、寄存器映射或历史采集值 |
| `ROLLER01_IP` / `ROLLER02_IP` | 辊床 PLC 通信地址 | 仅在对应功能明确接入时使用 |
| `LAST_QUERY_DATE` / `SYNC_FLAG` | 数据同步时间 / 同步开关 | 属于现有同步程序配置；本系统不据此启动同步，也不擅自回写 |

接入时仅按所需配置键读取，不向前端暴露整张配置表；缺失或重复键应明确报错，
不能随意选择一条。截图中的 Token、Secret 和密码值不录入项目文档或代码。

此表是**连接与认证配置**，不是设备电耗、气耗明细。魔盒配置也不等于前面
能管平台 `Account/Login`、`CollectValue` 接口的配置：不能将魔盒 Token
当成能管 `AuthId`，不能将 `MODBUS_GATEWAY` 直接代替能管 API 地址。
三块电表及气表与实际切割机的绑定、读数单位和采样周期仍需分别确认。

## 仍需确认

1. FTP服务器地址、端口、根目录、账号和传输模式；
2. `.GEN` 文件能否直接由魔盒接收，或是否需要格式转换；
3. 魔盒任务领取/完成记录所在的接口或数据表；
4. 领取记录中的人员ID如何关联HR公司的工号；
5. PDM `Custom6` 推送成功后的正式状态文本；
6. 生产任务同步程序是否写入 `OUT_TASK_ID`、`MAGIC_BOX_TASK_ID` 和 `FILE_NAME`。
