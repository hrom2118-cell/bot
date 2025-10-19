# Используем официальный образ Python
FROM python:3.10-slim

# Установка системных зависимостей и Supervisor
# ИСПРАВЛЕНО: Добавлены build-essential, python3-dev и libatlas-base-dev 
# для корректной компиляции численных библиотек (pandas/numpy).
RUN apt-get update && apt-get install -y \
    supervisor \
    build-essential \
    python3-dev \
    libatlas-base-dev \
    # Очистка кэша apt-get для уменьшения размера образа
    && rm -rf /var/lib/apt/lists/*

# Установка рабочей директории
WORKDIR /app

# Копирование файлов в контейнер
COPY requirements.txt .
COPY bot-macd.py bot-sqzmom.py ./
COPY app.py ./
COPY supervisord.conf ./
COPY templates/ templates/

# Установка зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Открытие порта для веб-панели (gunicorn)
EXPOSE 8080

# Запуск Supervisor
CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]
