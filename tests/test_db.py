from pathlib import Path

import pytest

from bot.db import Database


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


async def test_default_model_is_grok(db: Database) -> None:
    model = await db.upsert_user(1, "neo", "Нео")
    assert model == "grok"
    assert await db.get_model(1) == "grok"


async def test_switch_model_and_history(db: Database) -> None:
    await db.upsert_user(7, "trinity", "Тринити")
    await db.set_model(7, "gpt")
    assert await db.get_model(7) == "gpt"
    await db.add_message(7, "user", "привет")
    await db.add_message(7, "assistant", "йо")
    history = await db.history(7, 10)
    assert history == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "йо"},
    ]


async def test_trim_and_clear(db: Database) -> None:
    await db.upsert_user(3, "morpheus", "Морфеус")
    for i in range(10):
        await db.add_message(3, "user", f"m{i}")
    await db.trim_history(3, 4)
    history = await db.history(3, 20)
    assert [row["content"] for row in history] == ["m6", "m7", "m8", "m9"]
    await db.clear_history(3)
    assert await db.history(3, 20) == []
