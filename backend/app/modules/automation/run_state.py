"""Operations shared by the schedule and delivery repositories on their active connection."""
import json

from psycopg import Connection


def read_run(connection: Connection, run_id: str, *, for_update: bool = False) -> dict | None:
    row = connection.execute(
        "SELECT * FROM scheduled_report_runs WHERE run_id = %s" + (" FOR UPDATE" if for_update else ""), (run_id,)
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
    item["attach_report"] = bool(item["attach_report"])
    return item


def update_run(connection: Connection, run_id: str, status: str, *, now: str, error: str | None = None) -> None:
    row = connection.execute(
        """
        UPDATE scheduled_report_runs
        SET status = %s, last_error = %s, updated_at = %s
        WHERE run_id = %s AND status <> 'cancelled' RETURNING schedule_id
        """,
        (status, error, now, run_id),
    ).fetchone()
    if row is None:
        return
    if status == 'failed':
        connection.execute("""UPDATE email_deliveries SET status='failed',last_error=%s,updated_at=%s
            WHERE run_id=%s AND status='analysis_wait'""", (error, now, run_id))
    if row["schedule_id"]:
        connection.execute(
            """
            UPDATE report_schedules SET last_status = %s, updated_at = %s
            WHERE schedule_id = %s
            """,
            (status, now, row["schedule_id"]),
        )
