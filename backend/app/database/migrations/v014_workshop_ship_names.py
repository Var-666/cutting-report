"""Store ship display names separately from the original project identifier."""
import sqlite3


def upgrade(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute('PRAGMA table_info(workshop_report_items)')}
    if 'ship_no' not in columns:
        connection.execute('ALTER TABLE workshop_report_items ADD COLUMN ship_no TEXT')
        # Existing rows used the project identifier for both fields; keep that legacy snapshot intact.
        connection.execute('UPDATE workshop_report_items SET ship_no = project_name')
