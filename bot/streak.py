"""Чистая логика огонька (стрика), без Telegram и базы."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional


class FireStatus(str, Enum):
    """Состояние огонька у пары на конкретный день."""

    LIT_TODAY = "lit_today"
    WAITING_ME = "waiting_me"
    WAITING_THEM = "waiting_them"
    WAITING_BOTH = "waiting_both"
    COLD = "cold"


@dataclass(frozen=True)
class PairState:
    streak: int = 0
    last_streak_date: Optional[date] = None
    a_wrote_date: Optional[date] = None
    b_wrote_date: Optional[date] = None


@dataclass(frozen=True)
class StreakResult:
    state: PairState
    ignited: bool
    expired: bool
    previous_streak: int


def expire_if_needed(state: PairState, today: date) -> tuple[PairState, bool]:
    """Гасит огонёк, если оба не написали вчера (пропуск больше одного дня)."""
    if state.last_streak_date is None:
        return state, False
    missed = (today - state.last_streak_date).days > 1
    if missed and state.streak > 0:
        return (
            PairState(
                streak=0,
                last_streak_date=None,
                a_wrote_date=state.a_wrote_date,
                b_wrote_date=state.b_wrote_date,
            ),
            True,
        )
    if missed:
        return (
            PairState(
                streak=0,
                last_streak_date=None,
                a_wrote_date=state.a_wrote_date,
                b_wrote_date=state.b_wrote_date,
            ),
            False,
        )
    return state, False


def register_write(state: PairState, is_user_a: bool, today: date) -> StreakResult:
    """Учитывает сообщение и при необходимости зажигает огонёк на сегодня."""
    previous_streak = state.streak
    state, expired = expire_if_needed(state, today)

    if is_user_a:
        state = PairState(
            streak=state.streak,
            last_streak_date=state.last_streak_date,
            a_wrote_date=today,
            b_wrote_date=state.b_wrote_date,
        )
    else:
        state = PairState(
            streak=state.streak,
            last_streak_date=state.last_streak_date,
            a_wrote_date=state.a_wrote_date,
            b_wrote_date=today,
        )

    ignited = False
    both_today = state.a_wrote_date == today and state.b_wrote_date == today
    if both_today and state.last_streak_date != today:
        if state.last_streak_date == today - timedelta(days=1):
            new_streak = state.streak + 1
        else:
            new_streak = 1
        state = PairState(
            streak=new_streak,
            last_streak_date=today,
            a_wrote_date=state.a_wrote_date,
            b_wrote_date=state.b_wrote_date,
        )
        ignited = True

    return StreakResult(
        state=state,
        ignited=ignited,
        expired=expired,
        previous_streak=previous_streak,
    )


def fire_status(state: PairState, is_user_a: bool, today: date) -> FireStatus:
    """Статус огонька с точки зрения конкретного пользователя."""
    state, _ = expire_if_needed(state, today)
    my_date = state.a_wrote_date if is_user_a else state.b_wrote_date
    their_date = state.b_wrote_date if is_user_a else state.a_wrote_date
    i_wrote = my_date == today
    they_wrote = their_date == today

    if state.last_streak_date == today:
        return FireStatus.LIT_TODAY
    if i_wrote and they_wrote:
        return FireStatus.LIT_TODAY
    if i_wrote:
        return FireStatus.WAITING_THEM
    if they_wrote:
        return FireStatus.WAITING_ME
    if state.streak > 0 and state.last_streak_date == today - timedelta(days=1):
        return FireStatus.WAITING_BOTH
    return FireStatus.COLD


def fire_emoji(streak: int) -> str:
    if streak <= 0:
        return "💨"
    if streak < 3:
        return "🔥"
    if streak < 7:
        return "🔥🔥"
    if streak < 14:
        return "🔥🔥🔥"
    if streak < 30:
        return "💥"
    return "❤️‍🔥"


def status_label(status: FireStatus, streak: int) -> str:
    emoji = fire_emoji(streak)
    if status == FireStatus.LIT_TODAY:
        return f"{emoji} {streak} · сегодня горит"
    if status == FireStatus.WAITING_ME:
        return f"{emoji} {streak} · напиши, чтобы сохранить"
    if status == FireStatus.WAITING_THEM:
        return f"{emoji} {streak} · ждём ответа"
    if status == FireStatus.WAITING_BOTH:
        return f"{emoji} {streak} · напишите оба сегодня"
    if streak > 0:
        return f"{emoji} {streak}"
    return "💨 нет огонька"
