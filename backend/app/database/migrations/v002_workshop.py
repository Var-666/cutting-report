"""Baseline adoption: preserve existing schema/data upgrades; append future migrations instead of editing."""
from app.database.migrations.runner import execute_script


def upgrade(connection):
    execute_script(connection,
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'worker')),
            auth_source TEXT NOT NULL DEFAULT 'local',
            external_id TEXT,
            group_no TEXT,
            company_no TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_active_name
            ON users(active, display_name);

        CREATE TABLE IF NOT EXISTS auth_sessions (
            session_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires
            ON auth_sessions(expires_at);

        CREATE TABLE IF NOT EXISTS workshop_reports (
            report_id TEXT PRIMARY KEY,
            report_date TEXT NOT NULL,
            source_mode TEXT NOT NULL,
            scope_machine_no TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            generated_by_id TEXT NOT NULL,
            generated_by_name TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(generated_by_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_workshop_reports_generated
            ON workshop_reports(generated_at DESC);

        CREATE TABLE IF NOT EXISTS workshop_report_items (
            report_item_id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            project_name TEXT,
            segment_name TEXT,
            program_no TEXT,
            cut_length TEXT,
            groove_length TEXT,
            work_time TEXT,
            production_time TEXT,
            remark TEXT,
            UNIQUE(report_id, task_id),
            FOREIGN KEY(report_id) REFERENCES workshop_reports(report_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_workshop_items_report_order
            ON workshop_report_items(report_id, ordinal);

        CREATE TABLE IF NOT EXISTS workshop_confirmations (
            task_id TEXT NOT NULL,
            scope_machine_no TEXT NOT NULL,
            report_id TEXT,
            worker_id TEXT NOT NULL,
            worker_name TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(scope_machine_no, task_id),
            FOREIGN KEY(worker_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_workshop_confirmations_worker
            ON workshop_confirmations(worker_id, confirmed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_workshop_confirmations_retention
            ON workshop_confirmations(updated_at, task_id);
        """
    )
    user_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    for column, definition in (
        ("auth_source", "TEXT NOT NULL DEFAULT 'local'"),
        ("external_id", "TEXT"),
        ("group_no", "TEXT"),
        ("company_no", "TEXT"),
    ):
        if column not in user_columns:
            connection.execute(
                f"ALTER TABLE users ADD COLUMN {column} {definition}"
            )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_identity
        ON users(auth_source, external_id)
        WHERE external_id IS NOT NULL
        """
    )
    report_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(workshop_reports)"
        ).fetchall()
    }
    if "scope_machine_no" not in report_columns:
        connection.execute(
            "ALTER TABLE workshop_reports ADD COLUMN scope_machine_no TEXT"
        )
    if "snapshot_revision_floor" not in report_columns:
        connection.execute(
            "ALTER TABLE workshop_reports ADD COLUMN snapshot_revision_floor INTEGER NOT NULL DEFAULT 1"
        )
    item_columns = {row["name"] for row in connection.execute("PRAGMA table_info(workshop_report_items)")}
    if "production_date" not in item_columns:
        connection.execute("ALTER TABLE workshop_report_items ADD COLUMN production_date TEXT")
    if "snapshot_revision" not in item_columns:
        connection.execute("ALTER TABLE workshop_report_items ADD COLUMN snapshot_revision INTEGER NOT NULL DEFAULT 1")
    connection.execute(
        """UPDATE workshop_report_items SET production_date = (
           SELECT report_date FROM workshop_reports r
           WHERE r.report_id = workshop_report_items.report_id
        ) WHERE production_date IS NULL"""
    )
    confirmation_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(workshop_confirmations)"
        ).fetchall()
    }
    if "report_id" not in confirmation_columns:
        connection.execute(
            "ALTER TABLE workshop_confirmations ADD COLUMN report_id TEXT"
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workshop_confirmations_report
            ON workshop_confirmations(report_id, confirmed_at DESC)
        """
    )
    execute_script(connection,
        """
        CREATE TABLE IF NOT EXISTS workshop_confirmation_audits (
            audit_id TEXT PRIMARY KEY,
            scope_machine_no TEXT NOT NULL,
            task_id TEXT NOT NULL,
            report_id TEXT,
            action TEXT NOT NULL CHECK(action IN (
                'confirm', 'worker_revoke', 'admin_revoke', 'admin_reassign'
            )),
            previous_worker_id TEXT,
            previous_worker_name TEXT,
            new_worker_id TEXT,
            new_worker_name TEXT,
            reason TEXT NOT NULL,
            operated_by_id TEXT NOT NULL,
            operated_by_name TEXT NOT NULL,
            operated_by_role TEXT NOT NULL,
            operated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_workshop_confirmation_audits_task
            ON workshop_confirmation_audits(task_id, operated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_workshop_confirmation_audits_report
            ON workshop_confirmation_audits(report_id, operated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_workshop_confirmation_audits_retention
            ON workshop_confirmation_audits(operated_at, audit_id);

        CREATE TABLE IF NOT EXISTS admin_role_audits (
            audit_id TEXT PRIMARY KEY,
            subject_user_id TEXT NOT NULL,
            subject_external_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('grant', 'revoke')),
            operated_by_id TEXT NOT NULL,
            operated_by_name TEXT NOT NULL,
            operated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_role_audits_subject
            ON admin_role_audits(subject_user_id, operated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_admin_role_audits_retention
            ON admin_role_audits(operated_at, audit_id);
        """
    )
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_workshop_reports_device_date
        ON workshop_reports(report_date, scope_machine_no COLLATE NOCASE)""")
