from __future__ import annotations

from pathlib import Path
from typing import Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT 'grok',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База не подключена")
        return self._conn

    async def upsert_user(
        self,
        user_id: int,
        username: Optional[str],
        first_name: str,
    ) -> str:
        cur = await self.conn.execute(
            "SELECT model FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, model)
                VALUES (?, ?, ?, 'grok')
                """,
                (user_id, username, first_name or ""),
            )
            await self.conn.commit()
            return "grok"
        await self.conn.execute(
            """
            UPDATE users SET username = ?, first_name = ?
            WHERE user_id = ?
            """,
            (username, first_name or "", user_id),
        )
        await self.conn.commit()
        return str(row["model"])

    async def get_model(self, user_id: int) -> str:
        cur = await self.conn.execute(
            "SELECT model FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        return str(row["model"]) if row else "grok"

    async def set_model(self, user_id: int, model: str) -> None:
        await self.conn.execute(
            "UPDATE users SET model = ? WHERE user_id = ?",
            (model, user_id),
        )
        await self.conn.commit()

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        await self.conn.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        await self.conn.commit()

    async def history(self, user_id: int, limit: int) -> list[dict[str, str]]:
        cur = await self.conn.execute(
            """
            SELECT role, content FROM (
                SELECT id, role, content
                FROM messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) t
            ORDER BY id ASC
            """,
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    async def trim_history(self, user_id: int, keep: int) -> None:
        await self.conn.execute(
            """
            DELETE FROM messages
            WHERE user_id = ?
              AND id NOT IN (
                  SELECT id FROM messages
                  WHERE user_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (user_id, user_id, keep),
        )
        await self.conn.commit()

    async def clear_history(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await self.conn.commit()
