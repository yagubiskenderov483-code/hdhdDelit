from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


BTN_GPT = "🧠 ChatGPT"
BTN_GROK = "⚡ Grok"
BTN_BOTH = "🔥 Оба сразу"
BTN_RESET = "🧹 Новый чат"
BTN_HELP = "ℹ️ Помощь"


def main_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=BTN_GPT),
        KeyboardButton(text=BTN_GROK),
    )
    builder.row(
        KeyboardButton(text=BTN_BOTH),
        KeyboardButton(text=BTN_RESET),
    )
    builder.row(KeyboardButton(text=BTN_HELP))
    return builder.as_markup(resize_keyboard=True)
