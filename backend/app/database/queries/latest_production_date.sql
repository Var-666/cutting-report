WITH source AS (
    SELECT
        TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(CONVERT(varchar(64), [END_TIME], 126))), '')) AS production_date
    FROM [dbo].[task]
    WHERE [STATUS] = 2
      AND COALESCE([WORK_TIME], 0) >= 0
      AND UPPER(LTRIM(RTRIM([MACHINE_NO]))) = UPPER(?)
)
SELECT MAX(production_date) AS latest_date
FROM source
WHERE production_date <= ?
