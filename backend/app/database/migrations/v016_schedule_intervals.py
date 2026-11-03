"""Remember cadence without changing existing deadlines or queued task identities."""


def upgrade(connection):
    connection.execute('ALTER TABLE background_schedules ADD COLUMN interval REAL')
