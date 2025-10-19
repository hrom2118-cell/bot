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

# Проверка доступа к PyPI
RUN pip install --no-cache-dir --index-url https://pypi.org/simple/ --trusted-host pypi.org pip

# Установка зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Открытие порта для веб-панели (gunicorn)
EXPOSE 8080

# Запуск Supervisor
CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]
