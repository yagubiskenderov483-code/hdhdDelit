# Dual brain — Telegram AI

Помощник в Telegram на **ChatGPT** и **Grok**. Можно болтать с одной моделью или спросить обеих сразу.

Бот: [@TelegaFireBot](https://t.me/TelegaFireBot)

## Что умеет

- 🧠 ChatGPT — спокойные чёткие ответы
- ⚡ Grok — прямее, с характером
- 🔥 Оба сразу — два ответа на один вопрос
- память диалога, голосовые (Whisper) и фото
- кнопка «Новый чат» сбрасывает историю

## Запуск

Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

В `.env`:

```
BOT_TOKEN=токен_от_BotFather
OPENAI_API_KEY=ключ_openai
XAI_API_KEY=ключ_xai
OPENAI_MODEL=gpt-4o-mini
XAI_MODEL=grok-4.6
```

Ключи:

- OpenAI — https://platform.openai.com/api-keys
- xAI (Grok) — https://console.x.ai/

```bash
python -m bot
```

## Команды

| Команда | Что делает |
| --- | --- |
| `/start` | Старт |
| `/gpt` | Режим ChatGPT |
| `/grok` | Режим Grok |
| `/both` | Спросить обоих |
| `/reset` | Новый чат |
| `/help` | Помощь |

## Тесты

```bash
pytest
```
