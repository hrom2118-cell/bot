FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        supervisor \
        build-essential \
        libatlas-base-dev && \
    rm -rf /var/lib/apt/lists/*

# Установка рабочей директории
WORKDIR /app

# Копирование файлов в контейнер
COPY requirements.txt .
COPY bot-macd.py bot-sqzmom.py ./
COPY app.py ./
COPY supervisord.conf ./
COPY templates/ templates/

# Проверка доступа к PyPI (опционально)
RUN apt-get update && apt-get install -y curl && \
    curl -I https://pypi.org/simple/flask/ && \
    rm -rf /var/lib/apt/lists/*

# Установка зависимостей с таймаутом и повторами
RUN pip install --no-cache-dir --timeout 300 --retries 3 -r requirements.txt

# Открытие порта для веб-панели (gunicorn)
EXPOSE 8080

# Запуск Supervisor
CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]
