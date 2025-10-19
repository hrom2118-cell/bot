# Используем официальный образ Python
FROM python:3.10-slim

# Установка системных зависимостей и Supervisor.
# Все команды apt-get объединены в один RUN для максимальной стабильности, 
# включая build-essential, python3-dev и libatlas-base-dev для численных библиотек.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    supervisor \
    build-essential \
    python3-dev \
    libatlas-base-dev \
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
