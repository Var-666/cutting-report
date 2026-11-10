from __future__ import annotations
from psycopg import IntegrityError

from typing import Any
from app.database.postgres import PostgresRepository, lock_key

from uuid import uuid4
from app.database.identity_security import AuthenticationError, hash_password, verify_password

class UserRepository(PostgresRepository):
    @staticmethod
    def public_user(row: dict[str, Any]) -> dict[str, Any]:
        keys = set(row.keys())
        auth_source = row["auth_source"] if "auth_source" in keys else "local"
        company_no = row["company_no"] if "company_no" in keys else None
        group_no = row["group_no"] if "group_no" in keys else None
        return {
            "user_id": row["user_id"],
            "username": ((group_no or "").strip() or (company_no or "").strip() or row["username"])
            if auth_source == "hr_database" else row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "auth_source": auth_source,
            "external_id": row["external_id"] if "external_id" in keys else None,
            "group_no": group_no,
            "company_no": company_no,
            "active": bool(row["active"]),
            "created_at": row["created_at"],
        }

    def admin_count(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM users WHERE role = 'admin'"
                ).fetchone()['count']
            )

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        role: str,
        now: str,
    ) -> dict[str, Any]:
        user_id = f"usr_{uuid4().hex[:16]}"
        try:
            with self.connect() as connection, connection.transaction():
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, username, display_name, password_hash, role,
                        active, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                    """,
                    (
                        user_id,
                        username.strip(),
                        display_name.strip(),
                        hash_password(password),
                        role,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE user_id = %s", (user_id,)
                ).fetchone()
        except IntegrityError as exc:
            raise ValueError("登录账号已存在") from exc
        return self.public_user(row)

    def bootstrap_admin(
        self, *, username: str, display_name: str, password: str, now: str
    ) -> dict[str, Any]:
        user_id = f"usr_{uuid4().hex[:16]}"
        with self.connect() as connection:
            lock_key(connection, 'users:admin-membership')
            if connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role = 'admin'"
            ).fetchone()['count']:
                raise AuthenticationError("系统已经完成账号初始化")
            connection.execute(
                """
                INSERT INTO users (
                    user_id, username, display_name, password_hash, role,
                    active, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, 'admin', 1, %s, %s)
                """,
                (
                    user_id,
                    username.strip(),
                    display_name.strip(),
                    hash_password(password),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE user_id = %s", (user_id,)
            ).fetchone()
        return self.public_user(row)

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE sqlite_nocase(username) = sqlite_nocase(%s)
                  AND active = 1
                  AND auth_source = 'local'
                """,
                (username.strip(),),
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise AuthenticationError("账号或密码错误")
        return self.public_user(row)

    def upsert_external_worker(
        self,
        *,
        external_id: str,
        company_no: str,
        group_no: str,
        display_name: str,
        now: str,
    ) -> dict[str, Any]:
        """Create/update the local identity used by sessions and confirmations.

        No HR password is stored. The internal username is based on the stable HR
        identifier so a company-number change does not break history.
        """
        with self.connect() as connection:
            lock_key(connection, 'users:external:' + external_id)
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE auth_source = 'hr_database' AND external_id = %s
                """,
                (external_id,),
            ).fetchone()
            if row:
                connection.execute(
                    """
                    UPDATE users
                    SET display_name = %s, group_no = %s, company_no = %s,
                        active = 1, updated_at = %s
                    WHERE user_id = %s
                    """,
                    (display_name, group_no, company_no, now, row["user_id"]),
                )
                user_id = row["user_id"]
            else:
                user_id = f"usr_{uuid4().hex[:16]}"
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, username, display_name, password_hash, role,
                        auth_source, external_id, group_no, company_no,
                        active, created_at, updated_at
                    ) VALUES (%s, %s, %s, '', 'worker', 'hr_database', %s, %s, %s, 1, %s, %s)
                    """,
                    (
                        user_id,
                        f"hr:{external_id}",
                        display_name,
                        external_id,
                        group_no,
                        company_no,
                        now,
                        now,
                    ),
                )
            saved = connection.execute(
                "SELECT * FROM users WHERE user_id = %s", (user_id,)
            ).fetchone()
        return self.public_user(saved)

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY active DESC, display_name, username"
            ).fetchall()
        return [self.public_user(row) for row in rows]

    def active_user(self, user_id: str) -> dict[str, Any] | None:
        # SSO keeps this lock through profile refresh; avoid SHARE-to-UPDATE upgrades.
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM users WHERE user_id=%s AND active=1 FOR UPDATE', (user_id,)).fetchone()
        return self.public_user(row) if row else None

    def active_external_user(self, external_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE auth_source='hr_database' AND external_id=%s AND active=1 FOR UPDATE",
                (external_id,),
            ).fetchone()
        return self.public_user(row) if row else None

    def refresh_external_profile(self, user_id: str, identity: dict[str, str], *, now: str) -> bool:
        with self.connect() as connection:
            return connection.execute(
                """UPDATE users SET display_name=%s, group_no=%s, company_no=%s, updated_at=%s
                   WHERE user_id=%s AND auth_source='hr_database' AND external_id=%s AND active=1
                   RETURNING user_id""",
                (identity['display_name'], identity['group_no'], identity['company_no'], now,
                 user_id, identity['external_id']),
            ).fetchone() is not None

    def verify_local_user_password(self, user_id: str, password: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT password_hash FROM users
                WHERE user_id = %s AND auth_source = 'local' AND active = 1
                """,
                (user_id,),
            ).fetchone()
        return bool(row and verify_password(password, row["password_hash"]))

    def list_admins(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM users
                WHERE role = 'admin' AND active = 1
                ORDER BY auth_source, display_name, username
                """
            ).fetchall()
            admins = []
            for row in rows:
                admin = self.public_user(row)
                audit = connection.execute(
                    """
                    SELECT operated_by_name, operated_at
                    FROM admin_role_audits
                    WHERE subject_user_id = %s
                    ORDER BY operated_at DESC
                    LIMIT 1
                    """,
                    (row["user_id"],),
                ).fetchone()
                admin["permission_changed_by"] = (
                    audit["operated_by_name"] if audit else None
                )
                admin["permission_changed_at"] = audit["operated_at"] if audit else None
                admins.append(admin)
        return admins

    def grant_external_admin(
        self,
        *,
        identity: dict[str, str],
        operator: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        external_id = identity["external_id"].strip()
        company_no = identity["company_no"].strip()
        display_name = identity["display_name"].strip()
        group_no = (identity.get("group_no") or "").strip()
        if not external_id or not company_no or not display_name or not group_no:
            raise ValueError("HR人员信息不完整")

        with self.connect() as connection:
            lock_key(connection, 'users:admin-membership')
            lock_key(connection, 'users:external:' + external_id)
            existing = connection.execute(
                """
                SELECT * FROM users
                WHERE auth_source = 'hr_database' AND external_id = %s
                """,
                (external_id,),
            ).fetchone()
            already_admin = bool(existing and existing["role"] == "admin")
            if existing:
                user_id = existing["user_id"]
                connection.execute(
                    """
                    UPDATE users
                    SET display_name = %s, group_no = %s, company_no = %s,
                        role = 'admin', active = 1, updated_at = %s
                    WHERE user_id = %s
                    """,
                    (
                        display_name,
                        group_no,
                        company_no,
                        now,
                        user_id,
                    ),
                )
            else:
                user_id = f"usr_{uuid4().hex[:16]}"
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id, username, display_name, password_hash, role,
                        auth_source, external_id, group_no, company_no,
                        active, created_at, updated_at
                    ) VALUES (%s, %s, %s, '', 'admin', 'hr_database', %s, %s, %s, 1, %s, %s)
                    """,
                    (
                        user_id,
                        f"hr:{external_id}",
                        display_name,
                        external_id,
                        group_no,
                        company_no,
                        now,
                        now,
                    ),
                )

            if not already_admin:
                connection.execute(
                    """
                    INSERT INTO admin_role_audits (
                        audit_id, subject_user_id, subject_external_id, action,
                        operated_by_id, operated_by_name, operated_at
                    ) VALUES (%s, %s, %s, 'grant', %s, %s, %s)
                    """,
                    (
                        f"aar_{uuid4().hex[:16]}",
                        user_id,
                        external_id,
                        operator["user_id"],
                        operator["display_name"],
                        now,
                    ),
                )
            saved = connection.execute(
                "SELECT * FROM users WHERE user_id = %s", (user_id,)
            ).fetchone()
        return self.public_user(saved)

    def revoke_external_admin(
        self,
        user_id: str,
        *,
        operator: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            lock_key(connection, 'users:admin-membership')
            target = connection.execute(
                "SELECT * FROM users WHERE user_id = %s AND role = 'admin' AND active = 1",
                (user_id,),
            ).fetchone()
            if target is None:
                raise ValueError("管理员不存在或权限已撤销")
            if target["auth_source"] != "hr_database":
                raise ValueError("应急管理员不能在此撤销")
            if target["user_id"] == operator["user_id"]:
                raise ValueError("不能撤销当前登录管理员自己的权限")
            if connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND active = 1"
            ).fetchone()['count'] <= 1:
                raise ValueError("必须至少保留一个有效管理员")

            connection.execute(
                "UPDATE users SET role = 'worker', updated_at = %s WHERE user_id = %s",
                (now, user_id),
            )
            connection.execute(
                """
                INSERT INTO admin_role_audits (
                    audit_id, subject_user_id, subject_external_id, action,
                    operated_by_id, operated_by_name, operated_at
                ) VALUES (%s, %s, %s, 'revoke', %s, %s, %s)
                """,
                (
                    f"aar_{uuid4().hex[:16]}",
                    user_id,
                    target["external_id"],
                    operator["user_id"],
                    operator["display_name"],
                    now,
                ),
            )
            saved = connection.execute(
                "SELECT * FROM users WHERE user_id = %s", (user_id,)
            ).fetchone()
        return self.public_user(saved)
