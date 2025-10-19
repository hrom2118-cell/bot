FROM python:3.11-slim

# Установка рабочей директории
WORKDIR /app

# Копирование файлов в контейнер
COPY requirements.txt .
COPY bot-macd.py bot-sqzmom.py ./
COPY app.py ./
COPY templates/ templates/

# Установка зависимостей
RUN pip install --no-cache-dir --timeout 300 --retries 3 -r requirements.txt

# Открытие порта для веб-панели (gunicorn)
EXPOSE 8080

# Запуск веб-приложения через gunicorn (например)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
