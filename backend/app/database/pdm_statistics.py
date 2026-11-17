"""Read-only PDM statistics, separate from NC instruction browsing."""
from datetime import timedelta
from typing import Any

from app.core.sql_capacity import sql_identity
from app.database.sqlserver import SqlServerClient
from app.models.pdm_statistics import PDM_TYPES, PdmBranchFilters, PdmStatisticsQuery


class PdmStatisticsRepository:
    PLAN_VALUE = "NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(100), d.ArchivedTargetDate, 126))), N'')"
    ACTUAL_VALUE = "NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(100), d.ArchivedFeedBackDate, 126))), N'')"
    PLAN_DATE = f'TRY_CONVERT(date, {PLAN_VALUE})'
    ACTUAL_DATE = f'TRY_CONVERT(date, {ACTUAL_VALUE})'
    FROM = '''
        FROM dbo.PDM_DesignDocumentInfo AS d
        JOIN dbo.PDM_DocumentType AS t ON d.DocumentTypeID = t.ID
        LEFT JOIN dbo.BasicConfig_ProjectInfo AS p ON d.ProjectInfoID = p.ID
        LEFT JOIN dbo.PDM_DesignProfessional AS f ON d.DesignProfessionalID = f.ID
    '''

    def __init__(self, client: SqlServerClient) -> None:
        self.client = client

    def _fetch(self, sql: str, parameters: list[Any]) -> list[dict[str, Any]]:
        with sql_identity(None):
            return self.client.fetch_all(sql, tuple(parameters))

    @staticmethod
    def _in(expression: str, values: list[str], parameters: list[Any]) -> str:
        parameters.extend(values)
        return f"{expression} IN ({','.join('?' for _ in values)})" if values else '1=0'

    def _types(self, kind: str, filters: PdmBranchFilters, parameters: list[Any]) -> str:
        clauses = []
        for label, (code, name, _) in PDM_TYPES[kind].items():
            if not filters.types or label in filters.types:
                clauses.append('(t.DesignDocType = ? AND t.Name = ?)')
                parameters.extend([code, name])
        return 'd.ISAbandoned = 0 AND (' + ' OR '.join(clauses) + ')'

    @staticmethod
    def fields(kind: str) -> dict[str, str]:
        return {
            'projects': "COALESCE(CONVERT(nvarchar(200), d.ProjectInfoID), N'')",
            'professions': "COALESCE(CONVERT(nvarchar(200), d.DesignProfessionalID), N'')",
            'responsible_people': "COALESCE(CONVERT(nvarchar(200), d.ResponsiblePerson), N'')",
            **({
                'levels': "COALESCE(CONVERT(nvarchar(200), d.Custom12), N'')",
                'areas': "COALESCE(CONVERT(nvarchar(200), d.Custom11), N'')",
                # Historical PDM rows mix boolean text and Chinese labels; blanks are unspecified.
                'manufactory': """CASE UPPER(LTRIM(RTRIM(CONVERT(nvarchar(200), d.Custom14))))
                    WHEN N'TRUE' THEN N'是' WHEN N'是' THEN N'是'
                    WHEN N'FALSE' THEN N'否' WHEN N'否' THEN N'否'
                    ELSE COALESCE(CONVERT(nvarchar(200), d.Custom14), N'') END""",
            } if kind == 'equipment' else {
                'batches': "COALESCE(CONVERT(nvarchar(200), d.Custom14), N'')",
                'areas': "COALESCE(CONVERT(nvarchar(200), d.Custom16), N'')",
                'classifications': "COALESCE(CONVERT(nvarchar(200), d.Custom17), N'')",
                'weights': "COALESCE(CONVERT(nvarchar(200), d.Custom7), N'')",
            }),
        }

    def options(self, kind: str, filters: PdmBranchFilters) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        where = self._types(kind, filters, parameters)
        fields = self.fields(kind)
        profession_label = "COALESCE(NULLIF(CONVERT(nvarchar(1000), f.Name), N''), N'未填写')"
        values = ','.join(f"(N'{key}', {expression}, {profession_label if key == 'professions' else expression})"
                          for key, expression in fields.items() if key != 'projects')
        if filters.projects:
            where += ' AND ' + self._in(fields['projects'], filters.projects, parameters)
        if filters.professions:
            where += " AND (v.field = N'professions' OR " + self._in(fields['professions'], filters.professions, parameters) + ')'
        return self._fetch(f'''SELECT DISTINCT v.field, v.value, v.label
            {self.FROM} CROSS APPLY (VALUES {values}) AS v(field, value, label)
            WHERE {where}
            UNION ALL
            SELECT N'projects' AS field, CONVERT(nvarchar(200), p.ID) AS value, CONVERT(nvarchar(1000), p.ProjectInfoName)
                + CASE WHEN NULLIF(p.ShipNo, N'') IS NULL THEN N'' ELSE N' · ' + p.ShipNo END AS label
            FROM dbo.BasicConfig_ProjectInfo AS p
            WHERE p.ID IS NOT NULL AND NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(1000), p.ProjectInfoName))), N'') IS NOT NULL
            ORDER BY field, label, value''', parameters)

    def summary(self, kind: str, filters: PdmBranchFilters, query: PdmStatisticsQuery) -> list[dict[str, Any]]:
        parameters: list[Any] = [query.end_date, query.end_date]
        where = self._types(kind, filters, parameters)
        # A blank selected date falls back to the other date; two blanks belong to every interval.
        primary, primary_value, fallback = ((self.ACTUAL_DATE, self.ACTUAL_VALUE, self.PLAN_DATE)
                                            if query.date_basis == 'actual'
                                            else (self.PLAN_DATE, self.PLAN_VALUE, self.ACTUAL_DATE))
        where += f''' AND (
            ({primary} >= ? AND {primary} < ?)
            OR ({primary_value} IS NULL AND {fallback} >= ? AND {fallback} < ?)
            OR ({self.PLAN_VALUE} IS NULL AND {self.ACTUAL_VALUE} IS NULL)
        )'''
        end_exclusive = query.end_date + timedelta(days=1)
        parameters.extend([query.start_date, end_exclusive, query.start_date, end_exclusive])
        for key, expression in self.fields(kind).items():
            values = getattr(filters, key)
            if values or (key == 'responsible_people' and values is not None):
                where += ' AND ' + self._in(expression, values, parameters)
        return self._fetch(f'''SELECT t.Name AS type_name,
            CONVERT(nvarchar(200), d.ProjectInfoID) AS project_id,
            COALESCE(p.ProjectInfoName, N'未填写') AS project_name,
            CONVERT(nvarchar(200), d.DesignProfessionalID) AS profession_id,
            COALESCE(f.Name, N'未填写') AS profession_name,
            COUNT_BIG(*) AS total,
            SUM(CONVERT(bigint, CASE WHEN {self.ACTUAL_DATE} <= ? THEN 1 ELSE 0 END)) AS completed,
            SUM(CONVERT(bigint, CASE WHEN {self.ACTUAL_DATE} <= ?
                AND ({self.PLAN_VALUE} IS NULL OR {self.ACTUAL_DATE} <= {self.PLAN_DATE}) THEN 1 ELSE 0 END)) AS on_time
            {self.FROM} WHERE {where}
            GROUP BY t.Name, d.ProjectInfoID, p.ProjectInfoName, d.DesignProfessionalID, f.Name
            ORDER BY t.Name, p.ProjectInfoName, f.Name''', parameters)

    def overview(self, kind: str) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        where = self._types(kind, PdmBranchFilters(), parameters)
        return self._fetch(f'''SELECT COUNT_BIG(*) AS total,
            COALESCE(SUM(CONVERT(bigint, CASE WHEN {self.ACTUAL_DATE} IS NOT NULL THEN 1 ELSE 0 END)), 0) AS completed,
            COALESCE(SUM(CONVERT(bigint, CASE WHEN {self.ACTUAL_DATE} IS NOT NULL
                AND ({self.PLAN_VALUE} IS NULL OR {self.ACTUAL_DATE} <= {self.PLAN_DATE})
                THEN 1 ELSE 0 END)), 0) AS on_time
            FROM dbo.PDM_DesignDocumentInfo AS d
            JOIN dbo.PDM_DocumentType AS t ON d.DocumentTypeID = t.ID
            WHERE {where}''', parameters)

    def group_pairs(self, kind: str, filters: PdmBranchFilters) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        where = self._types(kind, filters, parameters)
        fields = self.fields(kind)
        for key in ('projects', 'professions'):
            if getattr(filters, key):
                where += ' AND ' + self._in(fields[key], getattr(filters, key), parameters)
        return self._fetch(f'''SELECT DISTINCT {fields['projects']} AS project_id,
            COALESCE(p.ProjectInfoName, N'未填写') AS project_name,
            COALESCE(p.ShipNo, N'') AS ship_no,
            {fields['professions']} AS profession_id,
            COALESCE(f.Name, N'未填写') AS profession_name
            {self.FROM} WHERE {where}
            ORDER BY ship_no, project_name, profession_name''', parameters)
