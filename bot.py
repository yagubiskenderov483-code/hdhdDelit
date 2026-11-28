"""
Фабрика аккаунтов — отдельный бот.

Задача: добавлять Telegram-аккаунты по номеру+коду и копить их в ОДИН растущий
файл `.backup`, который напрямую импортируется в трекер (тот же формат, тот же
SESSION_BACKUP_KEY). Нумерация session_NNN продолжается от загруженного набора.

Поток:
  1) (опционально) пришли боту стартовый .backup (например 206 аккаунтов) —
     бот загрузит их и продолжит нумерацию со следующего номера.
  2) /add  ->  номер  ->  код из Telegram  ->  (пароль 2FA, если есть).
  3) На успехе бот добавляет аккаунт в коллекцию и присылает ОБНОВЛЁННЫЙ .backup.
  4) /backup — получить текущий файл в любой момент. /count — сколько аккаунтов.

ВСЕ секреты берутся из переменных окружения (репозиторий публичный — в код не писать):
  BOT_TOKEN, API_ID, API_HASH, SESSION_BACKUP_KEY, ADMIN_IDS, START_INDEX
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import random
import re
import zlib
from pathlib import Path

from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, Message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("factory")

# ----------------------------------------------------------------- конфиг (env)
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
API_ID = int(os.environ.get("API_ID") or "0")
API_HASH = (os.environ.get("API_HASH") or "").strip()
BACKUP_KEY = (os.environ.get("SESSION_BACKUP_KEY") or "").strip()
ADMIN_IDS = {int(x) for x in re.split(r"[,\s]+", os.environ.get("ADMIN_IDS", "").strip()) if x.isdigit()}
START_INDEX = int(os.environ.get("START_INDEX") or "207")
STATE_FILE = Path(os.environ.get("STATE_FILE") or "/tmp/factory_accounts.json")
BACKUP_NAME = os.environ.get("BACKUP_NAME") or "accounts.backup"

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан (переменная окружения)")
if not API_ID or not API_HASH:
    raise SystemExit("API_ID / API_HASH не заданы (переменные окружения)")
if not BACKUP_KEY:
    raise SystemExit("SESSION_BACKUP_KEY не задан — без него .backup не совместим с трекером")

# ----------------------------------------------------- .backup: формат трекера
# Файл трекера = Fernet.encrypt( b"Z1" + zlib.compress( json ) ), ключ выведен из
# SESSION_BACKUP_KEY. Воспроизводим байт-в-байт, чтобы трекер импортировал напрямую.
_MAX_JSON = 64 * 1024 * 1024


def _key(k: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("gift-tracker:" + k).encode()).digest()))


def _fernet() -> MultiFernet | Fernet:
    # шифруем основным ключом (SESSION_BACKUP_KEY); при чтении пробуем и ключ из токена
    keys = [k for k in dict.fromkeys([BACKUP_KEY, BOT_TOKEN]) if k]
    return MultiFernet([_key(k) for k in keys]) if len(keys) > 1 else _key(keys[0])


def _decompress(raw: bytes) -> bytes:
    return zlib.decompress(raw)


def pack_backup(accounts: list[dict]) -> bytes:
    """accounts -> зашифрованный .backup, готовый к импорту трекером."""
    payload = {"sessions": accounts}
    blob = b"Z1" + zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 6)
    return _fernet().encrypt(blob)


def unpack_backup(blob: bytes) -> list[dict]:
    """Прочитать .backup (трекерный или наш) -> список аккаунтов [{name,string,api_id,api_hash}]."""
    raw = _fernet().decrypt(blob)          # бросит InvalidToken при чужом ключе
    if raw[:2] == b"Z1":
        raw = _decompress(raw[2:])
    if len(raw) > _MAX_JSON:
        raise ValueError("backup слишком большой")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        raise ValueError("это не файл резервной копии")
    out = []
    for it in data["sessions"]:
        if isinstance(it, dict) and isinstance(it.get("string"), str) and it["string"] and it.get("name"):
            out.append(it)
    return out


# ----------------------------------------------------------- коллекция аккаунтов
class Store:
    def __init__(self) -> None:
        self.accounts: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if STATE_FILE.exists():
                self.accounts = json.loads(STATE_FILE.read_text())
                log.info("Загружено из кэша: %d аккаунт(ов)", len(self.accounts))
        except Exception as e:
            log.warning("Кэш не прочитан: %s", e)

    def _save(self) -> None:
        try:
            STATE_FILE.write_text(json.dumps(self.accounts))
        except Exception as e:
            log.warning("Кэш не сохранён: %s", e)

    @staticmethod
    def _num(name: str) -> int:
        m = re.search(r"(\d+)\s*$", name or "")
        return int(m.group(1)) if m else -1

    def next_index(self) -> int:
        mx = max((self._num(a.get("name", "")) for a in self.accounts), default=START_INDEX - 1)
        return max(mx + 1, START_INDEX)

    def strings(self) -> set[str]:
        return {a["string"] for a in self.accounts if a.get("string")}

    def add(self, string: str) -> str:
        idx = self.next_index()
        name = f"session_{idx:03d}"
        self.accounts.append({"name": name, "string": string, "api_id": API_ID, "api_hash": API_HASH})
        self._save()
        return name

    def seed(self, incoming: list[dict]) -> int:
        """Добавить аккаунты из присланного .backup (без дублей по строке сессии)."""
        have = self.strings()
        added = 0
        for it in incoming:
            if it["string"] in have:
                continue
            # нормализуем: гарантируем api_id/api_hash (трекеру они нужны при импорте)
            it.setdefault("api_id", API_ID)
            it.setdefault("api_hash", API_HASH)
            self.accounts.append(it)
            have.add(it["string"])
            added += 1
        self._save()
        return added


store = Store()

# ------------------------------------------------------------------- FSM / бот
# Реалистичные профили Telegram Desktop (device_model, system_version, app_version).
# Логин с таким профилем выглядит как настоящий клиент, а не бот — меньше шанс, что Telegram
# сразу отзовёт сессию свежего аккаунта. Случайный выбор на аккаунт, чтобы не были одинаковы.
_DEVICE_PROFILES = [
    ("Desktop", "Windows 10", "5.7.3 x64"),
    ("Desktop", "Windows 11", "5.8.3 x64"),
    ("PC 64bit", "Windows 10", "5.6.3 x64"),
    ("MacBookPro18,1", "macOS 14.5", "11.6.2"),
    ("MacBookAir10,1", "macOS 13.6", "11.5.1"),
    ("Desktop", "Ubuntu 22.04", "5.7.3"),
]

router = Router()
pending: dict[int, dict] = {}        # uid -> {client, phone, hash}


class Add(StatesGroup):
    phone = State()
    code = State()
    password = State()


def is_admin(uid: int | None) -> bool:
    return bool(uid) and (not ADMIN_IDS or uid in ADMIN_IDS)


async def _cleanup(uid: int) -> None:
    p = pending.pop(uid, None)
    if p and p.get("client"):
        try:
            await p["client"].disconnect()
        except Exception:
            pass


async def _send_backup(bot: Bot, chat_id: int, caption: str) -> None:
    blob = pack_backup(store.accounts)
    file = BufferedInputFile(blob, filename=BACKUP_NAME)
    await bot.send_document(chat_id, file, caption=caption)


@router.message(CommandStart())
async def start(m: Message) -> None:
    if not is_admin(m.from_user.id):
        return
    await m.answer(
        "<b>Фабрика аккаунтов</b>\n\n"
        f"Сейчас в наборе: <b>{len(store.accounts)}</b> аккаунт(ов). Следующий номер: "
        f"<b>session_{store.next_index():03d}</b>\n\n"
        "• Пришли стартовый <b>.backup</b> файлом — подхвачу и продолжу нумерацию.\n"
        "• <b>/add</b> — добавить аккаунт (номер → код).\n"
        "• <b>/backup</b> — получить текущий файл.\n"
        "• <b>/count</b> — сколько аккаунтов.\n\n"
        "После каждого добавления пришлю обновлённый .backup — его заливаешь в трекер."
    )


@router.message(Command("count"))
async def count(m: Message) -> None:
    if not is_admin(m.from_user.id):
        return
    await m.answer(f"Аккаунтов: <b>{len(store.accounts)}</b>. Следующий: session_{store.next_index():03d}")


@router.message(Command("backup"))
async def backup(m: Message) -> None:
    if not is_admin(m.from_user.id):
        return
    if not store.accounts:
        await m.answer("Набор пуст — пока нечего выгружать.")
        return
    await _send_backup(m.bot, m.chat.id, f"Текущий набор: {len(store.accounts)} аккаунт(ов).")


@router.message(F.document)
async def on_backup_file(m: Message) -> None:
    if not is_admin(m.from_user.id):
        return
    doc = m.document
    if not doc or not (doc.file_name or "").lower().endswith((".backup", ".bak")):
        return
    if (doc.file_size or 0) > 19 * 1024 * 1024:
        await m.answer("Файл больше 19 МБ — Telegram не даст боту его скачать.")
        return
    note = await m.answer("Читаю файл…")
    try:
        buf = io.BytesIO()
        await m.bot.download(doc, destination=buf)
        incoming = unpack_backup(buf.getvalue())
    except InvalidToken:
        await note.edit_text("Файл не расшифровывается — он зашифрован другим ключом (нужен тот же SESSION_BACKUP_KEY, что у трекера).")
        return
    except Exception as e:
        await note.edit_text(f"Не похоже на .backup трекера: {type(e).__name__}")
        return
    added = store.seed(incoming)
    await note.edit_text(
        f"Загружено из файла: <b>{added}</b> новых (в наборе теперь {len(store.accounts)}).\n"
        f"Следующий номер: <b>session_{store.next_index():03d}</b>.\n\n"
        "Теперь /add — добавляй аккаунты."
    )


@router.message(Command("add"))
async def add_start(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    await _cleanup(m.from_user.id)
    await state.clear()
    await state.set_state(Add.phone)
    await m.answer(
        f"<b>Добавление аккаунта</b> (будет session_{store.next_index():03d})\n\n"
        "Шаг 1/3. Отправь <b>номер телефона</b> в формате +79991234567\n/cancel — отмена"
    )


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    await _cleanup(m.from_user.id)
    await state.clear()
    await m.answer("Отменено. /add — начать заново.")


@router.message(Add.phone, F.text)
async def add_phone(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    phone = re.sub(r"[^\d+]", "", m.text or "")
    ph = (phone[:5] + "***" + phone[-2:]) if len(phone) > 8 else "***"
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        await m.answer("Некорректный номер. Пример: +79991234567")
        return
    await _cleanup(m.from_user.id)
    await m.answer("⏳ Подключаюсь к Telegram и запрашиваю код…")
    # Реалистичный профиль устройства (Telegram Desktop), случайный на аккаунт. "Factory"/"1.0"
    # выглядели как бот — Telegram такие логины флагает и отзывает. Мимикрируем под настоящий клиент.
    dev = random.choice(_DEVICE_PROFILES)
    client = TelegramClient(StringSession(), API_ID, API_HASH,
                            connection_retries=1, timeout=15,
                            device_model=dev[0], system_version=dev[1], app_version=dev[2],
                            lang_code="en", system_lang_code="en")
    try:
        await asyncio.wait_for(client.connect(), 15)
        log.info("ADD [phone] %s api_id=%s — send_code", ph, API_ID)
        sent = await asyncio.wait_for(client.send_code_request(phone), 30)
    except errors.ApiIdInvalidError:
        await client.disconnect()
        await state.clear()
        await m.answer("Неверные API_ID / API_HASH (проверь переменные окружения).")
        return
    except (errors.PhoneNumberInvalidError, errors.PhoneNumberBannedError) as e:
        await client.disconnect()
        await m.answer(f"{type(e).__name__}. Другой номер или /cancel")
        return
    except errors.FloodWaitError as e:
        await client.disconnect()
        await state.clear()
        await m.answer(f"Telegram просит подождать {e.seconds} сек. Позже.")
        return
    except Exception as e:
        await client.disconnect()
        await state.clear()
        log.warning("ADD [phone] %s — send_code упал: %r", ph, e)
        await m.answer(f"Ошибка: {type(e).__name__}. /add — заново.")
        return
    ctype = type(getattr(sent, "type", None)).__name__
    log.info("ADD [code-sent] %s — КУДА: %s", ph, ctype)
    where = ("📱 в приложение Telegram этого аккаунта" if "App" in ctype
             else f"✉️ по SMS на {ph}" if "Sms" in ctype
             else "📞 звонком" if "Call" in ctype
             else f"канал: {ctype}")
    pending[m.from_user.id] = {"client": client, "phone": phone, "hash": sent.phone_code_hash}
    await state.set_state(Add.code)
    await m.answer(
        f"Шаг 2/3. Код отправлен — {where}.\n\n"
        "Отправь <b>код</b> с пробелами, например <code>1 2 3 4 5</code> "
        "(иначе Telegram заблокирует вход)."
    )


@router.message(Add.code, F.text)
async def add_code(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    code = re.sub(r"\D", "", m.text or "")
    if not code:
        await m.answer("Код — это цифры, например <code>1 2 3 4 5</code>.")
        return
    p = pending.get(m.from_user.id)
    if not p:
        await state.clear()
        await m.answer("Сессия входа потеряна. /add — заново.")
        return
    try:
        await p["client"].sign_in(phone=p["phone"], code=code, phone_code_hash=p["hash"])
    except errors.SessionPasswordNeededError:
        await state.set_state(Add.password)
        await m.answer("Шаг 3/3. Включена 2FA — отправь <b>пароль</b>.")
        return
    except errors.PhoneCodeInvalidError:
        await m.answer("Неверный код. Отправь ещё раз (с пробелами) или /cancel")
        return
    except errors.PhoneCodeExpiredError:
        await _cleanup(m.from_user.id)
        await state.clear()
        await m.answer("Код истёк. /add — заново.")
        return
    except Exception as e:
        await _cleanup(m.from_user.id)
        await state.clear()
        await m.answer(f"Ошибка входа: {type(e).__name__}")
        return
    await _finish(m, state)


@router.message(Add.password, F.text)
async def add_password(m: Message, state: FSMContext) -> None:
    if not is_admin(m.from_user.id):
        return
    pw = m.text or ""
    p = pending.get(m.from_user.id)
    if not p:
        await state.clear()
        await m.answer("Сессия входа потеряна. /add — заново.")
        return
    try:
        await p["client"].sign_in(password=pw)
    except errors.PasswordHashInvalidError:
        await m.answer("Неверный пароль 2FA. Отправь ещё раз или /cancel")
        return
    except Exception as e:
        await _cleanup(m.from_user.id)
        await state.clear()
        await m.answer(f"Ошибка 2FA: {type(e).__name__}")
        return
    await _finish(m, state)


async def _finish(m: Message, state: FSMContext) -> None:
    p = pending.pop(m.from_user.id, None)
    await state.clear()
    if not p:
        return
    client = p["client"]
    try:
        me = await client.get_me()
        string = client.session.save()
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        await m.answer(f"Вход прошёл, но сессию не сохранить: {type(e).__name__}. /add — заново.")
        return
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    if string in store.strings():
        await m.answer("Этот аккаунт уже в наборе (дубль). Пропускаю.")
        return
    name = store.add(string)
    log.info("ADD [ok] %s account_id=%s всего=%d", name, getattr(me, "id", "?"), len(store.accounts))
    await m.answer(
        f"✅ Добавлен <b>{name}</b> (ID {getattr(me, 'id', '?')}). "
        f"Всего в наборе: <b>{len(store.accounts)}</b>."
    )
    await _send_backup(m.bot, m.chat.id,
                       f"Обновлённый набор: {len(store.accounts)} аккаунт(ов). Залей в трекер.")


async def main() -> None:
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    me = await bot.get_me()
    log.info("Фабрика запущена: @%s, админы=%s, старт-индекс=%s, в наборе=%d",
             me.username, ADMIN_IDS or "любой", START_INDEX, len(store.accounts))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)


if __name__ == "__main__":
    asyncio.run(main())
