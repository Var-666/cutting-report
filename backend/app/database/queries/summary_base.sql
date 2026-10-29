/*
  完成任务汇总基础数据。
  工程号和船号使用同一编号：PROJECT_NAME 非空时优先，否则读取 PROJECT_CODE。
  参数顺序：设备编号、开始时间、结束时间、工程/船号、工程/船号、工程/船号、工程/船号。
*/
WITH task_source AS (
    SELECT
        t.*,
        COALESCE(NULLIF(LTRIM(RTRIM(t.[PROJECT_NAME])), ''), NULLIF(LTRIM(RTRIM(t.[PROJECT_CODE])), '')) AS project_number,
        COALESCE(
            TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(t.[OP_DATE])), ''), 23),
            CONVERT(date, t.[START_TIME])
        ) AS production_date
    FROM [dbo].[task] AS t
    WHERE t.[STATUS] = 2
      AND COALESCE(t.[WORK_TIME], 0) >= 0
      AND UPPER(LTRIM(RTRIM(t.[MACHINE_NO]))) = UPPER(?)
)
SELECT
    CONVERT(varchar(64), source.[TASK_ID]) AS task_id,
    source.production_date AS production_date,
    source.project_number AS project_code,
    source.project_number AS ship_no,
    NULLIF(LTRIM(RTRIM(source.[SEGMENT_NAME])), '') AS segment_name,
    NULLIF(LTRIM(RTRIM(source.[OP_PROGRAM])), '') AS program_no,
    CONVERT(decimal(18, 3), COALESCE(source.[OP_LENGTH_CUT], 0)) / 1000.0 AS cut_length,
    CONVERT(decimal(18, 3), COALESCE(source.[OP_LENGTH_GROOVE], 0)) / 1000.0 AS groove_length,
    CONVERT(decimal(18, 3), COALESCE(source.[WORK_TIME], 0)) AS work_time,
    source.[START_TIME] AS production_time,
    source.[END_TIME] AS source_updated_at
FROM task_source AS source
WHERE source.production_date >= CONVERT(date, ?)
  AND source.production_date < CONVERT(date, ?)
  AND (? IS NULL OR source.project_number = ?)
  AND (? IS NULL OR source.project_number = ?)
ORDER BY source.[START_TIME], source.[TASK_ID];
