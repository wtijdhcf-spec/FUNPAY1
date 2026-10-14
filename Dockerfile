FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOSTING=1 \
    DISABLE_TUNNEL=1

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Создание конфигурации если нет (на хостинге ключ приедет из env GOLDEN_KEY)
RUN if [ ! -f config.json ]; then cp config.example.json config.json; fi

EXPOSE 5000

CMD ["python", "main.py"]
