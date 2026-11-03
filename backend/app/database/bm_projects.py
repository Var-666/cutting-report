"""Read-only BM project directory; production task queries remain in their own database."""
from collections.abc import Sequence

from app.database.sqlserver import SqlServerClient


class BmProjectRepository:
    def __init__(self, client: SqlServerClient) -> None:
        self.client = client

    def ship_names(self, project_numbers: Sequence[str]) -> dict[str, str | None]:
        numbers = sorted({value.strip().upper() for value in project_numbers if value and value.strip()})
        matches: dict[str, list[str | None]] = {number: [] for number in numbers}
        for offset in range(0, len(numbers), 500):
            batch = numbers[offset:offset + 500]
            placeholders = ','.join('?' for _ in batch)
            rows = self.client.fetch_all(f'''
                SELECT ProjectNum AS project_number, ProjectName AS ship_name
                FROM dbo.BM_PROJECT
                WHERE UPPER(LTRIM(RTRIM(ProjectNum))) IN ({placeholders})
            ''', tuple(batch))
            for row in rows:
                number = str(row.get('project_number') or '').strip().upper()
                if number in matches:
                    name = str(row.get('ship_name') or '').strip() or None
                    matches[number].append(name)
        # Never pick an arbitrary row or multiply tasks when a source number becomes ambiguous.
        return {number: names[0] if len(names) == 1 else None for number, names in matches.items()}
