from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from app.database.sqlite import SQLiteDatabase, SQLiteRepository

import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import uuid4
from app.database.users import UserRepository

class SessionRepository(SQLiteRepository):
    @staticmethod
    def _session_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_session(
        self, user_id: str, *, now: datetime, hours: int
    ) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(hours=hours)
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?", (now.isoformat(),)
            )
            connection.execute(
                """
                INSERT INTO auth_sessions(session_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self._session_hash(token),
                    user_id,
                    now.isoformat(timespec="seconds"),
                    expires_at.isoformat(timespec="seconds"),
                ),
            )
        return token, expires_at.isoformat(timespec="seconds")

    def user_for_session(self, token: str | None, *, now: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT u.*
                FROM auth_sessions AS s
                JOIN users AS u ON u.user_id = s.user_id
                WHERE s.session_hash = ? AND s.expires_at > ? AND u.active = 1
                """,
                (self._session_hash(token), now),
            ).fetchone()
        return UserRepository.public_user(row) if row else None

    def revoke_user_sessions(self, user_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE session_hash = ?",
                (self._session_hash(token),),
            )
