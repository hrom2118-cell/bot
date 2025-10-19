import redis
from flask import Flask, render_template, request, redirect, url_for
import time
from datetime import datetime

# --- НАСТРОЙКИ ---
REDIS_HOST = 'redis'
REDIS_PORT = 6379
BOTS = ['macd_bot', 'sqzmom_bot']

app = Flask(__name__)
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

# --- УТИЛИТЫ ---
def get_bot_status(bot_id):
    """Получает статус и статистику бота из Redis."""
    status = r.hgetall(f'bot_status:{bot_id}')
    stats = r.hgetall(f'bot_stats:{bot_id}')
    summary = r.get(f'bot_summary:{bot_id}')
    
    # Парсинг данных
    running = status.get('running') == '1'
    in_position = status.get('in_position') == '1'
    
    last_update_ts = status.get('last_update')
    last_update = datetime.fromtimestamp(float(last_update_ts)).strftime('%H:%M:%S %d.%m') if last_update_ts else 'N/A'
    
    state_message = status.get('state_message', 'Active/Running') if running else (summary if summary else 'Stopped')
    
    # Вычисление времени работы
    start_time_ts = r.get(f'bot_start_time:{bot_id}')
    if running and start_time_ts:
        runtime_sec = time.time() - float(start_time_ts)
        runtime = str(int(runtime_sec // 3600)).zfill(2) + ':' + str(int((runtime_sec % 3600) // 60)).zfill(2) + ':' + str(int(runtime_sec % 60)).zfill(2)
    else:
        runtime = '00:00:00'

    return {
        'id': bot_id,
        'running': running,
        'in_position': in_position,
        'status_text': state_message,
        'last_update': last_update,
        'runtime': runtime,
        'stats': stats,
        'summary': summary
    }

# --- МАРШРУТЫ ---
@app.route('/')
def dashboard():
    """Главная страница с панелью управления."""
    bot_data = [get_bot_status(bot_id) for bot_id in BOTS]
    return render_template('dashboard.html', bots=bot_data)

@app.route('/command', methods=['POST'])
def command():
    """Обработка команд START/STOP."""
    bot_id = request.form.get('bot_id')
    action = request.form.get('action')
    
    if bot_id in BOTS and action in ['START', 'STOP']:
        try:
            # Отправка команды в Redis
            r.set(f'command:{bot_id}', action)
            
            if action == 'START':
                # Запись времени старта для расчета времени работы
                r.set(f'bot_start_time:{bot_id}', time.time())
                # Очистка предыдущего отчета
                r.delete(f'bot_summary:{bot_id}')
                r.delete(f'bot_stats:{bot_id}')
                
            return redirect(url_for('dashboard'))
        except Exception as e:
            return f"Redis Error: {e}", 500
    
    return "Invalid command", 400

if __name__ == '__main__':
    # Flask будет запущен через gunicorn/supervisord в Docker
    # Для локального тестирования можно использовать app.run()
    pass