WITH source AS (
    SELECT
        COALESCE(
            TRY_CONVERT(date, NULLIF(LTRIM(RTRIM([OP_DATE])), ''), 23),
            CONVERT(date, [START_TIME])
        ) AS production_date
    FROM [dbo].[task]
    WHERE [STATUS] = 2
      AND COALESCE([WORK_TIME], 0) >= 0
      AND UPPER(LTRIM(RTRIM([MACHINE_NO]))) = UPPER(?)
)
SELECT MAX(production_date) AS latest_date
FROM source
WHERE production_date <= ?
