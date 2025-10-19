FROM ubuntu:22.04

# Установка системных зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        supervisor \
        build-essential \
        python3-dev \
        libatlas-base-dev \
        python3-pip && \ 
    rm -rf /var/lib/apt/lists/*

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
