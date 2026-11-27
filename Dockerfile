FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

# Все секреты — через переменные окружения Render (репозиторий публичный!):
#   BOT_TOKEN, API_ID, API_HASH, SESSION_BACKUP_KEY, ADMIN_IDS, START_INDEX
CMD ["python", "-u", "bot.py"]
