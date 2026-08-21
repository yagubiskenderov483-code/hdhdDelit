from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite

from bot.streak import PairState


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS friendships (
    user_a           INTEGER NOT NULL,
    user_b           INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    invited_by       INTEGER NOT NULL,
    streak           INTEGER NOT NULL DEFAULT 0,
    last_streak_date TEXT,
    a_wrote_date     TEXT,
    b_wrote_date     TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_a, user_b),
    CHECK (user_a < user_b),
    FOREIGN KEY (user_a) REFERENCES users(user_id),
    FOREIGN KEY (user_b) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    user_id     INTEGER PRIMARY KEY,
    partner_id  INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_friendships_status ON friendships(status);
"""


def ordered_pair(user_id: int, other_id: int) -> tuple[int, int]:
    if user_id == other_id:
        raise ValueError("Нельзя создать пару с самим собой")
    return (user_id, other_id) if user_id < other_id else (other_id, user_id)


def parse_iso(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


def dump_iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


@dataclass
class User:
    user_id: int
    username: Optional[str]
    first_name: str

    def display_name(self) -> str:
        if self.username:
            return f"{self.first_name} (@{self.username})"
        return self.first_name or str(self.user_id)


@dataclass
class Friendship:
    user_a: int
    user_b: int
    status: str
    invited_by: int
    state: PairState

    def other_id(self, user_id: int) -> int:
        return self.user_b if user_id == self.user_a else self.user_a

    def is_user_a(self, user_id: int) -> bool:
        return user_id == self.user_a


@dataclass
class FriendRow:
    friend: User
    friendship: Friendship


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
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
    ) -> User:
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (user_id, (username or None), first_name or ""),
        )
        await self.conn.commit()
        return User(user_id=user_id, username=username or None, first_name=first_name or "")

    async def get_user(self, user_id: int) -> Optional[User]:
        cur = await self.conn.execute(
            "SELECT user_id, username, first_name FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        return self._user_from_row(row) if row else None

    async def get_user_by_username(self, username: str) -> Optional[User]:
        clean = username.lstrip("@").strip().lower()
        cur = await self.conn.execute(
            "SELECT user_id, username, first_name FROM users WHERE lower(username) = ?",
            (clean,),
        )
        row = await cur.fetchone()
        return self._user_from_row(row) if row else None

    async def get_friendship(self, user_id: int, other_id: int) -> Optional[Friendship]:
        a, b = ordered_pair(user_id, other_id)
        cur = await self.conn.execute(
            """
            SELECT user_a, user_b, status, invited_by, streak,
                   last_streak_date, a_wrote_date, b_wrote_date
            FROM friendships
            WHERE user_a = ? AND user_b = ?
            """,
            (a, b),
        )
        row = await cur.fetchone()
        return self._friendship_from_row(row) if row else None

    async def create_invite(self, from_id: int, to_id: int) -> Friendship:
        existing = await self.get_friendship(from_id, to_id)
        if existing:
            return existing
        a, b = ordered_pair(from_id, to_id)
        await self.conn.execute(
            """
            INSERT INTO friendships (user_a, user_b, status, invited_by)
            VALUES (?, ?, 'pending', ?)
            """,
            (a, b, from_id),
        )
        await self.conn.commit()
        friendship = await self.get_friendship(from_id, to_id)
        assert friendship is not None
        return friendship

    async def accept_friendship(self, user_id: int, other_id: int) -> Optional[Friendship]:
        friendship = await self.get_friendship(user_id, other_id)
        if friendship is None or friendship.status != "pending":
            return friendship
        if friendship.invited_by == user_id:
            return friendship
        await self.conn.execute(
            """
            UPDATE friendships
            SET status = 'accepted'
            WHERE user_a = ? AND user_b = ?
            """,
            (friendship.user_a, friendship.user_b),
        )
        await self.conn.commit()
        return await self.get_friendship(user_id, other_id)

    async def accept_by_invite_link(self, from_id: int, to_id: int) -> Friendship:
        """Ссылка-приглашение сразу делает пару друзьями."""
        existing = await self.get_friendship(from_id, to_id)
        a, b = ordered_pair(from_id, to_id)
        if existing is None:
            await self.conn.execute(
                """
                INSERT INTO friendships (user_a, user_b, status, invited_by)
                VALUES (?, ?, 'accepted', ?)
                """,
                (a, b, from_id),
            )
        elif existing.status != "accepted":
            await self.conn.execute(
                """
                UPDATE friendships
                SET status = 'accepted'
                WHERE user_a = ? AND user_b = ?
                """,
                (a, b),
            )
        await self.conn.commit()
        friendship = await self.get_friendship(from_id, to_id)
        assert friendship is not None
        return friendship

    async def decline_or_remove(self, user_id: int, other_id: int) -> bool:
        friendship = await self.get_friendship(user_id, other_id)
        if friendship is None:
            return False
        await self.conn.execute(
            "DELETE FROM friendships WHERE user_a = ? AND user_b = ?",
            (friendship.user_a, friendship.user_b),
        )
        await self.conn.execute(
            "UPDATE chat_sessions SET partner_id = NULL WHERE user_id IN (?, ?)",
            (user_id, other_id),
        )
        await self.conn.commit()
        return True

    async def save_state(self, friendship: Friendship, state: PairState) -> None:
        await self.conn.execute(
            """
            UPDATE friendships
            SET streak = ?, last_streak_date = ?, a_wrote_date = ?, b_wrote_date = ?
            WHERE user_a = ? AND user_b = ?
            """,
            (
                state.streak,
                dump_iso(state.last_streak_date),
                dump_iso(state.a_wrote_date),
                dump_iso(state.b_wrote_date),
                friendship.user_a,
                friendship.user_b,
            ),
        )
        await self.conn.commit()

    async def list_friends(self, user_id: int) -> list[FriendRow]:
        cur = await self.conn.execute(
            """
            SELECT
                f.user_a, f.user_b, f.status, f.invited_by, f.streak,
                f.last_streak_date, f.a_wrote_date, f.b_wrote_date,
                u.user_id, u.username, u.first_name
            FROM friendships f
            JOIN users u ON u.user_id = CASE
                WHEN f.user_a = ? THEN f.user_b ELSE f.user_a
            END
            WHERE (f.user_a = ? OR f.user_b = ?) AND f.status = 'accepted'
            ORDER BY f.streak DESC, u.first_name COLLATE NOCASE
            """,
            (user_id, user_id, user_id),
        )
        rows = await cur.fetchall()
        result: list[FriendRow] = []
        for row in rows:
            result.append(
                FriendRow(
                    friend=User(
                        user_id=row["user_id"],
                        username=row["username"],
                        first_name=row["first_name"],
                    ),
                    friendship=self._friendship_from_row(row),
                )
            )
        return result

    async def list_incoming(self, user_id: int) -> list[FriendRow]:
        cur = await self.conn.execute(
            """
            SELECT
                f.user_a, f.user_b, f.status, f.invited_by, f.streak,
                f.last_streak_date, f.a_wrote_date, f.b_wrote_date,
                u.user_id, u.username, u.first_name
            FROM friendships f
            JOIN users u ON u.user_id = f.invited_by
            WHERE (f.user_a = ? OR f.user_b = ?)
              AND f.status = 'pending'
              AND f.invited_by != ?
            """,
            (user_id, user_id, user_id),
        )
        rows = await cur.fetchall()
        return [
            FriendRow(
                friend=User(
                    user_id=row["user_id"],
                    username=row["username"],
                    first_name=row["first_name"],
                ),
                friendship=self._friendship_from_row(row),
            )
            for row in rows
        ]

    async def top_streaks(self, limit: int = 10) -> list[tuple[User, User, int]]:
        cur = await self.conn.execute(
            """
            SELECT
                f.streak,
                a.user_id AS a_id, a.username AS a_username, a.first_name AS a_name,
                b.user_id AS b_id, b.username AS b_username, b.first_name AS b_name
            FROM friendships f
            JOIN users a ON a.user_id = f.user_a
            JOIN users b ON b.user_id = f.user_b
            WHERE f.status = 'accepted' AND f.streak > 0
            ORDER BY f.streak DESC, f.last_streak_date DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
        result = []
        for row in rows:
            result.append(
                (
                    User(row["a_id"], row["a_username"], row["a_name"]),
                    User(row["b_id"], row["b_username"], row["b_name"]),
                    int(row["streak"]),
                )
            )
        return result

    async def set_partner(self, user_id: int, partner_id: Optional[int]) -> None:
        await self.conn.execute(
            """
            INSERT INTO chat_sessions (user_id, partner_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET partner_id = excluded.partner_id
            """,
            (user_id, partner_id),
        )
        await self.conn.commit()

    async def get_partner(self, user_id: int) -> Optional[int]:
        cur = await self.conn.execute(
            "SELECT partner_id FROM chat_sessions WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row or row["partner_id"] is None:
            return None
        return int(row["partner_id"])

    async def pairs_waiting_today(self, today: date) -> list[tuple[Friendship, User, User]]:
        yesterday = (today - timedelta(days=1)).isoformat()
        today_s = today.isoformat()
        cur = await self.conn.execute(
            """
            SELECT
                f.user_a, f.user_b, f.status, f.invited_by, f.streak,
                f.last_streak_date, f.a_wrote_date, f.b_wrote_date,
                a.user_id AS a_id, a.username AS a_username, a.first_name AS a_name,
                b.user_id AS b_id, b.username AS b_username, b.first_name AS b_name
            FROM friendships f
            JOIN users a ON a.user_id = f.user_a
            JOIN users b ON b.user_id = f.user_b
            WHERE f.status = 'accepted'
              AND f.streak > 0
              AND f.last_streak_date = ?
              AND (f.a_wrote_date IS NULL OR f.a_wrote_date != ?
                   OR f.b_wrote_date IS NULL OR f.b_wrote_date != ?)
            """,
            (yesterday, today_s, today_s),
        )
        rows = await cur.fetchall()
        result = []
        for row in rows:
            result.append(
                (
                    self._friendship_from_row(row),
                    User(row["a_id"], row["a_username"], row["a_name"]),
                    User(row["b_id"], row["b_username"], row["b_name"]),
                )
            )
        return result

    @staticmethod
    def _user_from_row(row: aiosqlite.Row) -> User:
        return User(
            user_id=int(row["user_id"]),
            username=row["username"],
            first_name=row["first_name"] or "",
        )

    @staticmethod
    def _friendship_from_row(row: aiosqlite.Row) -> Friendship:
        return Friendship(
            user_a=int(row["user_a"]),
            user_b=int(row["user_b"]),
            status=row["status"],
            invited_by=int(row["invited_by"]),
            state=PairState(
                streak=int(row["streak"]),
                last_streak_date=parse_iso(row["last_streak_date"]),
                a_wrote_date=parse_iso(row["a_wrote_date"]),
                b_wrote_date=parse_iso(row["b_wrote_date"]),
            ),
        )
