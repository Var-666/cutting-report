/*
  领导报表快照批量查询。
  参数：设备编号、开始月份第一天、结束月份下一月第一天（左闭右开）。
  生产表只扫描一次；#filtered_tasks 只存在于当前只读连接的 tempdb 会话。
*/
SET NOCOUNT ON;

DECLARE @machine_no nvarchar(128) = CAST(? AS nvarchar(128));
DECLARE @range_start date = CAST(? AS date);
DECLARE @range_end date = CAST(? AS date);

SELECT
    calculated.production_date,
    COALESCE(NULLIF(LTRIM(RTRIM(t.[PROJECT_NAME])), ''), NULLIF(LTRIM(RTRIM(t.[PROJECT_CODE])), ''), N'未标注工程') AS project_name,
    CONVERT(decimal(38, 3), COALESCE(t.[OP_LENGTH_CUT], 0)) AS cut_length_mm,
    CONVERT(decimal(38, 3), COALESCE(t.[OP_LENGTH_GROOVE], 0)) AS groove_length_mm,
    CONVERT(decimal(38, 0), COALESCE(t.[WORK_TIME], 0)) AS work_time_seconds,
    CASE WHEN NULLIF(LTRIM(RTRIM(t.[REMARK])), '') IS NULL THEN 0 ELSE 1 END AS has_remark
INTO #filtered_tasks
FROM [dbo].[task] AS t
CROSS APPLY (
    SELECT COALESCE(
        TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(t.[OP_DATE])), ''), 23),
        CONVERT(date, t.[START_TIME])
    ) AS production_date
) AS calculated
WHERE t.[STATUS] = 2
  AND COALESCE(t.[WORK_TIME], 0) >= 0
  AND UPPER(LTRIM(RTRIM(t.[MACHINE_NO]))) = UPPER(@machine_no)
  AND calculated.production_date >= @range_start
  AND calculated.production_date < @range_end;

SELECT
    production_date AS period_start,
    COUNT_BIG(*) AS task_count,
    SUM(cut_length_mm) / CONVERT(decimal(38, 3), 1000) AS cut_length_m,
    SUM(groove_length_mm) / CONVERT(decimal(38, 3), 1000) AS groove_length_m,
    SUM(work_time_seconds) AS work_time_seconds,
    SUM(has_remark) AS remark_count
FROM #filtered_tasks
GROUP BY production_date
ORDER BY production_date;

SELECT
    DATEFROMPARTS(YEAR(production_date), MONTH(production_date), 1) AS period_start,
    project_name,
    COUNT_BIG(*) AS task_count,
    SUM(cut_length_mm) / CONVERT(decimal(38, 3), 1000) AS cut_length_m,
    SUM(work_time_seconds) AS work_time_seconds
FROM #filtered_tasks
GROUP BY
    DATEFROMPARTS(YEAR(production_date), MONTH(production_date), 1),
    project_name
ORDER BY period_start, task_count DESC, project_name;
