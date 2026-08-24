/*
  领导报表：当月按日明细 + 最近 12 个月对比。
  参数：所选月份第一天（date）。
  统计口径：STATUS = 2；OP_DATE 无效时回退 START_TIME；长度毫米转米；工时单位秒。
*/
WITH report_parameters AS (
    SELECT CAST(? AS date) AS month_start
),
task_source AS (
    SELECT
        COALESCE(
            TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(t.[OP_DATE])), ''), 23),
            CONVERT(date, t.[START_TIME])
        ) AS production_date,
        CONVERT(decimal(38, 3), COALESCE(t.[OP_LENGTH_CUT], 0)) AS cut_length_mm,
        CONVERT(decimal(38, 3), COALESCE(t.[OP_LENGTH_GROOVE], 0)) AS groove_length_mm,
        CONVERT(decimal(38, 0), COALESCE(t.[WORK_TIME], 0)) AS work_time_seconds,
        NULLIF(LTRIM(RTRIM(t.[REMARK])), '') AS remark
    FROM [dbo].[task] AS t
    WHERE t.[STATUS] = 2
      AND COALESCE(t.[WORK_TIME], 0) >= 0
),
filtered_tasks AS (
    SELECT source.*
    FROM task_source AS source
    CROSS JOIN report_parameters AS parameters
    WHERE source.production_date >= DATEADD(month, -11, parameters.month_start)
      AND source.production_date < DATEADD(month, 1, parameters.month_start)
),
daily_summary AS (
    SELECT
        CAST('daily' AS varchar(10)) AS period_type,
        production_date AS period_start,
        COUNT_BIG(*) AS task_count,
        SUM(cut_length_mm) / CONVERT(decimal(38, 3), 1000) AS cut_length_m,
        SUM(groove_length_mm) / CONVERT(decimal(38, 3), 1000) AS groove_length_m,
        SUM(work_time_seconds) AS work_time_seconds,
        CASE
            WHEN SUM(CASE WHEN remark IS NULL THEN 0 ELSE 1 END) = 0 THEN NULL
            ELSE CONCAT(N'含 ', SUM(CASE WHEN remark IS NULL THEN 0 ELSE 1 END), N' 条任务备注')
        END AS remark
    FROM filtered_tasks
    CROSS JOIN report_parameters AS parameters
    WHERE production_date >= parameters.month_start
      AND production_date < DATEADD(month, 1, parameters.month_start)
    GROUP BY production_date
),
monthly_summary AS (
    SELECT
        CAST('monthly' AS varchar(10)) AS period_type,
        DATEFROMPARTS(YEAR(production_date), MONTH(production_date), 1) AS period_start,
        COUNT_BIG(*) AS task_count,
        SUM(cut_length_mm) / CONVERT(decimal(38, 3), 1000) AS cut_length_m,
        SUM(groove_length_mm) / CONVERT(decimal(38, 3), 1000) AS groove_length_m,
        SUM(work_time_seconds) AS work_time_seconds,
        CASE
            WHEN SUM(CASE WHEN remark IS NULL THEN 0 ELSE 1 END) = 0 THEN NULL
            ELSE CONCAT(N'含 ', SUM(CASE WHEN remark IS NULL THEN 0 ELSE 1 END), N' 条任务备注')
        END AS remark
    FROM filtered_tasks
    GROUP BY DATEFROMPARTS(YEAR(production_date), MONTH(production_date), 1)
),
combined AS (
    SELECT * FROM daily_summary
    UNION ALL
    SELECT * FROM monthly_summary
)
SELECT
    period_type,
    period_start,
    task_count,
    cut_length_m,
    groove_length_m,
    work_time_seconds,
    remark
FROM combined
ORDER BY
    CASE WHEN period_type = 'daily' THEN 0 ELSE 1 END,
    period_start;
