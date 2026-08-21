from __future__ import annotations

from datetime import date

from bot.streak import FireStatus, fire_emoji, fire_status, status_label
from bot.db import FriendRow, User


WELCOME = (
    "🔥 Привет! Это бот «Огонёк».\n\n"
    "Пишите друг другу каждый день — и огонёк растёт, как в ТикТоке.\n"
    "Пропустите день — огонёк погаснет.\n\n"
    "Правила простые:\n"
    "1. Добавь друга по @username или ссылке.\n"
    "2. Зайдите в чат и напишите хотя бы одно сообщение.\n"
    "3. Когда оба написали сегодня — огонёк +1.\n\n"
    "Скинь другу ссылку-приглашение ниже 👇"
)

HOW_IT_WORKS = (
    "🔥 Как работает огонёк\n\n"
    "Это стрик, как огонёк в ТикТоке или Snapchat:\n"
    "• оба друга должны написать друг другу хотя бы раз за день;\n"
    "• день считается по московскому времени;\n"
    "• если оба написали — огонёк увеличивается на 1;\n"
    "• если кто-то пропустил день — огонёк сбрасывается в 0.\n\n"
    "Сообщения идут через бота: фото, голосовые, стикеры и текст.\n"
    "Вечером напомним, если огонёк ещё не зажжён."
)

NEED_FRIEND = (
    "Сначала добавь друга: нажми «➕ Добавить друга» "
    "или пришли @username."
)

ASK_USERNAME = (
    "Пришли @username друга или перешли его контакт.\n"
    "Если его ещё нет в боте — лучше скинь ему свою ссылку."
)

CANCELLED = "Ок, отменил."


def invite_text(bot_username: str, user_id: int) -> str:
    link = f"https://t.me/{bot_username}?start=u{user_id}"
    return (
        f"🔗 Твоя ссылка-приглашение:\n{link}\n\n"
        "Друг переходит по ней — и вы сразу можете писать и копить огонёк."
    )


def friend_added(name: str) -> str:
    return f"✅ {name} теперь в друзьях. Заходи в чат и зажигайте огонёк!"


def request_sent(name: str) -> str:
    return f"📨 Заявка отправлена: {name}. Когда примет — сможете писать."


def incoming_request(name: str) -> str:
    return f"👋 {name} хочет копить с тобой огонёк. Принять?"


def no_friends() -> str:
    return "Пока нет друзей. Добавь кого-нибудь — и начните огонёк 🔥"


def friends_header(count: int) -> str:
    if count == 0:
        return no_friends()
    return f"🔥 Твои огоньки ({count}):\nвыбери, кому написать."


def friend_button_title(row: FriendRow, me_id: int, today: date) -> str:
    status = fire_status(row.friendship.state, row.friendship.is_user_a(me_id), today)
    streak = row.friendship.state.streak
    prefix = fire_emoji(streak) if streak else "💨"
    short = {
        FireStatus.LIT_TODAY: "горит",
        FireStatus.WAITING_ME: "напиши",
        FireStatus.WAITING_THEM: "ждём",
        FireStatus.WAITING_BOTH: "оба",
        FireStatus.COLD: "холодный",
    }[status]
    return f"{prefix} {row.friend.first_name} · {streak} · {short}"


def chat_opened(friend: User, row_status: str) -> str:
    return (
        f"💬 Чат с {friend.display_name()}\n"
        f"{row_status}\n\n"
        "Пиши сюда — я перешлю сообщение другу. "
        "Чтобы выйти, нажми «❌ Выйти из чата»."
    )


def incoming_header(sender: User, extra: str) -> str:
    return f"🔥 {sender.display_name()}\n{extra}"


def ignited_text(streak: int, friend_name: str) -> str:
    emoji = fire_emoji(streak)
    if streak == 1:
        return f"{emoji} Огонёк с {friend_name} зажжён! День 1 — не дайте ему погаснуть."
    if streak in (7, 14, 30, 100):
        return f"{emoji} Вау! Огонёк с {friend_name} уже {streak} дней подряд!"
    return f"{emoji} Огонёк с {friend_name}: {streak} дней подряд!"


def expired_text(friend_name: str, old_streak: int) -> str:
    return (
        f"💨 Огонёк с {friend_name} погас. "
        f"Было {old_streak} дн. Напишите оба сегодня — зажжёте снова."
    )


def top_text(items: list[tuple[User, User, int]]) -> str:
    if not items:
        return "🏆 Пока пусто. Будьте первыми, у кого загорится огонёк."
    lines = ["🏆 Топ огоньков\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (a, b, streak) in enumerate(items, start=1):
        mark = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(
            f"{mark} {a.first_name} + {b.first_name} — {fire_emoji(streak)} {streak}"
        )
    return "\n".join(lines)


def remind_text(friend: User, streak: int, status: FireStatus) -> str:
    if status == FireStatus.WAITING_ME:
        hint = "Друг уже написал. Ответь сегодня, иначе огонёк погаснет."
    elif status == FireStatus.WAITING_THEM:
        hint = "Ты уже написал. Напомни другу ответить."
    else:
        hint = "Сегодня ещё никто не написал. Зажгите огонёк до полуночи."
    return (
        f"⚠️ Огонёк с {friend.first_name} {fire_emoji(streak)} {streak} может погаснуть!\n"
        f"{hint}"
    )


def status_for(row: FriendRow, me_id: int, today: date) -> str:
    st = fire_status(row.friendship.state, row.friendship.is_user_a(me_id), today)
    return status_label(st, row.friendship.state.streak)
