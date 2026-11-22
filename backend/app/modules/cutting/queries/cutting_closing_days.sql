/* Split at midnight and 17:00 so daily and calendar cumulative windows share one read. */
WITH completed AS (
    SELECT
        TRY_CONVERT(datetime2(7), NULLIF(LTRIM(RTRIM(CONVERT(varchar(64), t.[END_TIME], 126))), '')) AS completed_at,
        CONVERT(decimal(38, 3), COALESCE(t.[OP_LENGTH_CUT], 0)) AS cut_length_mm,
        CONVERT(decimal(38, 3), COALESCE(t.[OP_LENGTH_GROOVE], 0)) AS groove_length_mm,
        CONVERT(decimal(38, 0), COALESCE(t.[WORK_TIME], 0)) AS work_time_seconds
    FROM [dbo].[task] AS t
    WHERE t.[STATUS] = 2
      AND COALESCE(t.[WORK_TIME], 0) >= 0
      AND UPPER(LTRIM(RTRIM(t.[MACHINE_NO]))) = UPPER(?)
), closing_days AS (
    SELECT DATEADD(hour, CASE WHEN DATEPART(hour, completed_at) >= 17 THEN 17 ELSE 0 END,
        CONVERT(datetime2(7), CONVERT(date, completed_at))) AS bucket_start,
        cut_length_mm, groove_length_mm, work_time_seconds
    FROM completed
    WHERE completed_at >= CAST(? AS datetime2(7)) AND completed_at < CAST(? AS datetime2(7))
)
SELECT bucket_start, COUNT_BIG(*) AS task_count,
    SUM(cut_length_mm) / CONVERT(decimal(38, 3), 1000) AS cut_length_m,
    SUM(groove_length_mm) / CONVERT(decimal(38, 3), 1000) AS groove_length_m,
    SUM(work_time_seconds) AS work_time_seconds
FROM closing_days
GROUP BY bucket_start;
