/*
  指定月份内，各设备每日完成任务的切割时长汇总。
  设备范围由服务层按 ENERGY_REPORT_EQUIPMENT_IDS 过滤。
  参数顺序：月份开始日期、下月开始日期。
*/
WITH task_source AS (
    SELECT
        COALESCE(
            TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(t.[OP_DATE])), ''), 23),
            CONVERT(date, t.[START_TIME])
        ) AS production_date,
        NULLIF(LTRIM(RTRIM(t.[MACHINE_NO])), '') AS equipment_id,
        CONVERT(decimal(18, 3), COALESCE(t.[WORK_TIME], 0)) AS work_time_seconds
    FROM [dbo].[task] AS t
    WHERE t.[STATUS] = 2
      AND COALESCE(t.[WORK_TIME], 0) >= 0
)
SELECT
    source.production_date AS report_date,
    source.equipment_id,
    COUNT_BIG(*) AS task_count,
    SUM(source.work_time_seconds) AS work_time_seconds
FROM task_source AS source
WHERE source.production_date >= CONVERT(date, ?)
  AND source.production_date < CONVERT(date, ?)
GROUP BY source.production_date, source.equipment_id
ORDER BY source.production_date, source.equipment_id;
