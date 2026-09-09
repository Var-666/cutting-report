/*
  车间生产明细。
  工程号和船号均按 PROJECT_NAME 原样映射。
  参数顺序：设备编号、开始时间、结束时间、工程/船号、工程/船号、工程/船号、工程/船号。
*/
WITH task_source AS (
    SELECT
        t.*,
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
    NULLIF(LTRIM(RTRIM(source.[PROJECT_NAME])), '') AS project_code,
    NULLIF(LTRIM(RTRIM(source.[PROJECT_NAME])), '') AS ship_no,
    NULLIF(LTRIM(RTRIM(source.[SEGMENT_NAME])), '') AS segment_name,
    NULLIF(LTRIM(RTRIM(source.[OP_PROGRAM])), '') AS program_no,
    CONVERT(decimal(18, 3), COALESCE(source.[OP_LENGTH_CUT], 0)) / 1000.0 AS cut_length,
    CONVERT(decimal(18, 3), COALESCE(source.[OP_LENGTH_GROOVE], 0)) / 1000.0 AS groove_length,
    CONVERT(decimal(18, 3), COALESCE(source.[WORK_TIME], 0)) AS work_time,
    NULLIF(LTRIM(RTRIM(source.[REMARK])), '') AS remark,
    source.[START_TIME] AS production_time
FROM task_source AS source
WHERE source.production_date >= CONVERT(date, ?)
  AND source.production_date < CONVERT(date, ?)
  AND (? IS NULL OR source.[PROJECT_NAME] = ?)
  AND (? IS NULL OR source.[PROJECT_NAME] = ?)
ORDER BY source.[START_TIME], source.[TASK_ID];
