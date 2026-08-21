from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.db import Database
from bot.streak import PairState, register_write


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


async def seed(db: Database) -> None:
    await db.upsert_user(1, "anna", "Анна")
    await db.upsert_user(2, "boris", "Борис")
    await db.accept_by_invite_link(1, 2)


async def test_invite_link_makes_friends(db: Database) -> None:
    await seed(db)
    friendship = await db.get_friendship(1, 2)
    assert friendship is not None
    assert friendship.status == "accepted"
    friends = await db.list_friends(1)
    assert len(friends) == 1
    assert friends[0].friend.first_name == "Борис"


async def test_username_lookup_is_case_insensitive(db: Database) -> None:
    await db.upsert_user(10, "CoolUser", "Кекс")
    found = await db.get_user_by_username("@COOLUSER")
    assert found is not None
    assert found.user_id == 10


async def test_pending_request_then_accept(db: Database) -> None:
    await db.upsert_user(1, "a", "A")
    await db.upsert_user(2, "b", "B")
    pending = await db.create_invite(1, 2)
    assert pending.status == "pending"
    incoming = await db.list_incoming(2)
    assert len(incoming) == 1
    accepted = await db.accept_friendship(2, 1)
    assert accepted is not None
    assert accepted.status == "accepted"


async def test_streak_persists_and_at_risk_pairs(db: Database) -> None:
    await seed(db)
    friendship = await db.get_friendship(1, 2)
    assert friendship is not None
    day = date(2026, 8, 20)
    state = register_write(PairState(), True, day).state
    state = register_write(state, False, day).state
    await db.save_state(friendship, state)

    today = day + timedelta(days=1)
    waiting = await db.pairs_waiting_today(today)
    assert len(waiting) == 1
    assert waiting[0][0].state.streak == 1

    already_done = await db.pairs_waiting_today(day)
    assert already_done == []


async def test_remove_friend(db: Database) -> None:
    await seed(db)
    assert await db.decline_or_remove(1, 2) is True
    assert await db.get_friendship(1, 2) is None
    assert await db.list_friends(1) == []
