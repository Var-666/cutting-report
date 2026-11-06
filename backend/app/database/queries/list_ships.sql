WITH source AS (
    SELECT
        COALESCE(NULLIF(LTRIM(RTRIM([PROJECT_NAME])), ''), NULLIF(LTRIM(RTRIM([PROJECT_CODE])), '')) AS project_name,
        COALESCE(
            TRY_CONVERT(date, NULLIF(LTRIM(RTRIM([OP_DATE])), ''), 23),
            CONVERT(date, [START_TIME])
        ) AS production_date
    FROM [dbo].[task]
    WHERE [STATUS] = 2
      AND UPPER(LTRIM(RTRIM([MACHINE_NO]))) = UPPER(?)
)
SELECT DISTINCT project_name
FROM source
WHERE production_date >= ? AND production_date < ?
  AND project_name IS NOT NULL
  AND (? IS NULL OR project_name = ?)
ORDER BY project_name
