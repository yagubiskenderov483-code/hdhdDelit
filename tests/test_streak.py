from datetime import date, timedelta

from bot.streak import (
    FireStatus,
    PairState,
    fire_emoji,
    fire_status,
    register_write,
)

DAY = date(2026, 8, 21)


def test_first_message_does_not_ignite() -> None:
    result = register_write(PairState(), is_user_a=True, today=DAY)
    assert result.ignited is False
    assert result.state.streak == 0
    assert result.state.a_wrote_date == DAY
    assert fire_status(result.state, True, DAY) is FireStatus.WAITING_THEM
    assert fire_status(result.state, False, DAY) is FireStatus.WAITING_ME


def test_both_write_same_day_starts_streak() -> None:
    after_a = register_write(PairState(), True, DAY).state
    result = register_write(after_a, False, DAY)
    assert result.ignited is True
    assert result.state.streak == 1
    assert result.state.last_streak_date == DAY
    assert fire_status(result.state, True, DAY) is FireStatus.LIT_TODAY


def test_second_day_increments() -> None:
    state = PairState(streak=1, last_streak_date=DAY, a_wrote_date=DAY, b_wrote_date=DAY)
    nxt = DAY + timedelta(days=1)
    state = register_write(state, True, nxt).state
    result = register_write(state, False, nxt)
    assert result.ignited is True
    assert result.state.streak == 2


def test_same_day_does_not_double_count() -> None:
    state = PairState(streak=3, last_streak_date=DAY, a_wrote_date=DAY, b_wrote_date=DAY)
    result = register_write(state, True, DAY)
    assert result.ignited is False
    assert result.state.streak == 3


def test_missed_day_resets_then_can_restart() -> None:
    state = PairState(streak=12, last_streak_date=DAY, a_wrote_date=DAY, b_wrote_date=DAY)
    later = DAY + timedelta(days=2)
    after_a = register_write(state, True, later)
    assert after_a.expired is True
    assert after_a.previous_streak == 12
    assert after_a.state.streak == 0
    result = register_write(after_a.state, False, later)
    assert result.ignited is True
    assert result.state.streak == 1


def test_waiting_both_while_streak_still_alive() -> None:
    state = PairState(streak=5, last_streak_date=DAY, a_wrote_date=DAY, b_wrote_date=DAY)
    nxt = DAY + timedelta(days=1)
    assert fire_status(state, True, nxt) is FireStatus.WAITING_BOTH


def test_one_wrote_today_streak_alive() -> None:
    state = PairState(streak=5, last_streak_date=DAY, a_wrote_date=DAY, b_wrote_date=DAY)
    nxt = DAY + timedelta(days=1)
    state = register_write(state, True, nxt).state
    assert fire_status(state, True, nxt) is FireStatus.WAITING_THEM
    assert fire_status(state, False, nxt) is FireStatus.WAITING_ME
    assert state.streak == 5


def test_only_one_person_never_creates_fire() -> None:
    state = PairState()
    for i in range(5):
        state = register_write(state, True, DAY + timedelta(days=i)).state
    assert state.streak == 0
    assert state.last_streak_date is None


def test_fire_emoji_levels() -> None:
    assert fire_emoji(0) == "💨"
    assert fire_emoji(1) == "🔥"
    assert fire_emoji(8) == "🔥🔥🔥"
    assert fire_emoji(30) == "❤️‍🔥"
