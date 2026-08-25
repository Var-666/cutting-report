/*
  领导报表当月工程分布。
  参数：所选月份第一天、下一月第一天。
*/
WITH task_source AS (
    SELECT
        COALESCE(
            TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(t.[OP_DATE])), ''), 23),
            CONVERT(date, t.[START_TIME])
        ) AS production_date,
        COALESCE(NULLIF(LTRIM(RTRIM(t.[PROJECT_NAME])), ''), N'未标注工程') AS project_name,
        CONVERT(decimal(38, 3), COALESCE(t.[OP_LENGTH_CUT], 0)) AS cut_length_mm,
        CONVERT(decimal(38, 0), COALESCE(t.[WORK_TIME], 0)) AS work_time_seconds
    FROM [dbo].[task] AS t
    WHERE t.[STATUS] = 2
      AND COALESCE(t.[WORK_TIME], 0) >= 0
)
SELECT
    project_name,
    COUNT_BIG(*) AS task_count,
    SUM(cut_length_mm) / CONVERT(decimal(38, 3), 1000) AS cut_length_m,
    SUM(work_time_seconds) AS work_time_seconds
FROM task_source
WHERE production_date >= ? AND production_date < ?
GROUP BY project_name
ORDER BY task_count DESC, project_name;
