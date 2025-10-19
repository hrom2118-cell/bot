import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
import websocket
import json
import time
import threading
import sys
import requests
from datetime import datetime, timezone, timedelta
import redis # <-- НОВЫЙ ИМПОРТ

# --- НАСТРОЙКИ (Обновлено для SQZMOM стратегии) ---
SYMBOL = 'ETHUSDT'
INTERVAL = Client.KLINE_INTERVAL_15MINUTE
HIGHER_INTERVAL = Client.KLINE_INTERVAL_15MINUTE # Не используется, но оставляем для структуры

# Настройки Squeeze Momentum
SQZ_BB_ДЛИНА = 30
SQZ_BB_МУЛЬТИФАКТОР = 1.8
SQZ_KC_ДЛИНА = 30
SQZ_KC_МУЛЬТИФАКТОР = 1.9
SQZ_ATR_ПЕРИОД = 14

# Настройки Риска и Прибыли
RISK_AMOUNT_USD = 1.00    # Фиксированный риск на сделку в USD
RISK_PERCENT_SL = 0.005   # 0.5% Стоп-Лосс от цены входа
PROFIT_PERCENT_TP = 0.015 # 1.5% Тейк-Профит от цены входа

# Общие настройки риска
DAILY_MAX_LOSS_PERCENT = 0.05
MAX_DRAWDOWN_PERCENT = 0.20

# Комиссии и Проскальзывание
COMMISSION_PERCENT = 0.001
SLIPPAGE_PERCENT = 0.0001 # <-- Изменено, чтобы соответствовать ПРОСКАЛЬЗЫВАНИЕ_ДОЛЯ из backtest

# --- НАСТРОЙКИ REDIS И ИДЕНТИФИКАТОР БОТА ---
BOT_ID = 'sqzmom_bot' # Уникальный ID бота (ИЗМЕНЕНО)
REDIS_HOST = 'redis' # Имя сервиса Redis в docker-compose
REDIS_PORT = 6379
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

# API клиент (публичный)
API_KEY = ''
API_SECRET = ''
client = Client(API_KEY, API_SECRET)

# =========================================================================
# --- ФУНКЦИИ УПРАВЛЕНИЯ (Без Telegram) ---
# =========================================================================

# Функция send_telegram_message УДАЛЕНА

def retry_api(max_attempts=3, delay=2):
    """Декоратор для повторных попыток API."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"Retry {attempt+1}/{max_attempts}: {e}")
                    time.sleep(delay * (2 ** attempt))
            raise Exception("Max retries exceeded")
        return wrapper
    return decorator

# --- УПРАВЛЕНИЕ ТОРГОВЫМ СЧЕТОМ (PaperAccount - Модифицировано) ---
class PaperAccount:
    """Класс для управления демо-счетом (Из bot.py, с небольшим изменением в PnL)."""
    def __init__(self, initial_balance):
        self.initial_balance = initial_balance
        self.balance_usdt = initial_balance
        self.daily_start_balance = initial_balance
        self.position = 0.0
        self.entry_price = 0.0
        self.stop_loss_level = 0.0
        self.take_profit_level = 0.0
        self.is_long = True
        self.is_in_position = False
        self.trade_history = []
        self.session_started = False
        self.daily_loss = 0.0
        self.last_hourly_report = time.time()
        self.last_position_size_usdt = 0.0

    def reset_daily(self):
        self.daily_start_balance = self.balance_usdt
        self.daily_loss = 0.0
        # send_telegram_message(f"Daily reset: Start balance {self.daily_start_balance:.2f} USDT") # УДАЛЕНО

    def check_limits(self, potential_loss):
        if self.balance_usdt <= self.initial_balance * (1 - MAX_DRAWDOWN_PERCENT):
            # send_telegram_message("Max drawdown reached! Stopping bot.") # УДАЛЕНО
            print("Max drawdown reached! Stopping bot.")
            self.session_started = False
            return False
        
        if abs(self.daily_loss) + potential_loss >= self.daily_start_balance * DAILY_MAX_LOSS_PERCENT:
            # send_telegram_message("Daily max loss reached! Stopping for today.") # УДАЛЕНО
            print("Daily max loss reached! Stopping for today.")
            return False
        return True

    def enter_position(self, current_price, is_long, position_size_usdt_entry, sl_level, tp_level, margin_usdt):
        if self.is_in_position or margin_usdt > self.balance_usdt:
            return False

        self.stop_loss_level = sl_level
        self.take_profit_level = tp_level
        
        direction = 1 if is_long else -1
        self.position = (position_size_usdt_entry / current_price) * direction # Объем в монетах
        self.entry_price = current_price # Фактическая цена входа с учетом проскальзывания
        self.is_long = is_long
        self.is_in_position = True
        self.last_position_size_usdt = position_size_usdt_entry # Запоминаем условный объем
        
        # Списание маржи и комиссий
        self.balance_usdt -= margin_usdt 
        commission = position_size_usdt_entry * COMMISSION_PERCENT
        self.balance_usdt -= commission

        msg = f"Enter {'LONG' if is_long else 'SHORT'}: Price {current_price:.4f} (w/ Slippage), SL {self.stop_loss_level:.4f}, TP {self.take_profit_level:.4f}"
        print(msg)
        # send_telegram_message(msg) # УДАЛЕНО
        self.update_redis_status(is_in_position=True) # <-- НОВОЕ
        return True

    def close_position(self, current_price, reason):
        if not self.is_in_position:
            return

        direction = 1 if self.is_long else -1
        pnl_usdt = abs(self.position) * (current_price - self.entry_price) * direction # PnL без учета затрат
        
        position_size_usdt_exit = abs(self.position) * current_price
        commission_exit = position_size_usdt_exit * COMMISSION_PERCENT
        pnl_usdt -= commission_exit

        self.balance_usdt += pnl_usdt
        self.daily_loss += min(0, pnl_usdt) 

        pnl_percent = (pnl_usdt / self.last_position_size_usdt) * 100 if self.last_position_size_usdt else 0

        self.trade_history.append({
            'entry_time': time.strftime("%H:%M:%S", time.localtime()),
            'entry_price': self.entry_price,
            'exit_price': current_price,
            'pnl_usdt': pnl_usdt,
            'pnl_percent': pnl_percent,
            'reason': reason,
            'type': 'LONG' if self.is_long else 'SHORT'
        })

        self.is_in_position = False
        self.position = 0.0
        self.last_position_size_usdt = 0.0

        msg = f"Close {'LONG' if self.is_long else 'SHORT'}: Price {current_price:.4f}, PnL {pnl_usdt:.2f} ({pnl_percent:.2f}%), Reason: {reason}\nBalance: {self.balance_usdt:.2f}"
        print(msg)
        # send_telegram_message(msg) # УДАЛЕНО
        self.update_redis_status(is_in_position=False) # <-- НОВОЕ
        
    def get_pnl(self, current_price):
        if self.is_in_position:
            direction = 1 if self.is_long else -1
            unrealized_pnl_usdt = abs(self.position) * (current_price - self.entry_price) * direction
            return unrealized_pnl_usdt, (unrealized_pnl_usdt / (abs(self.position) * self.entry_price)) * 100
        return 0.0, 0.0

    def update_redis_status(self, is_running=None, is_in_position=None):
        """Отправляет текущий статус бота в Redis.""" # <-- НОВАЯ ФУНКЦИЯ
        if is_running is None: is_running = self.session_started
        if is_in_position is None: is_in_position = self.is_in_position
        try:
            r.hset(f'bot_status:{BOT_ID}', mapping={
                'running': 1 if is_running else 0,
                'in_position': 1 if is_in_position else 0,
                'last_update': time.time()
            })
        except Exception as e:
            print(f"Redis status update error: {e}")

    def generate_report(self, current_price):
        """Генерирует отчет и отправляет его в Redis.""" # <-- МОДИФИЦИРОВАНО
        pnl_usdt, pnl_percent = self.get_pnl(current_price)
        equity = self.balance_usdt + (abs(self.position) * current_price if self.is_in_position else 0)
        
        # Обновление статистики в Redis
        try:
            r.hset(f'bot_stats:{BOT_ID}', mapping={
                'balance': f"{self.balance_usdt:.2f}",
                'equity': f"{equity:.2f}",
                'pnl_unrealized': f"{pnl_usdt:.2f}",
                'pnl_percent_unrealized': f"{pnl_percent:.2f}",
                'trades_count': len(self.trade_history),
                'session_pnl': f"{sum(t['pnl_usdt'] for t in self.trade_history):.2f}",
                'current_price': f"{current_price:.4f}", # Изменено для ETHUSDT
                'is_long': self.is_long,
                'entry_price': f"{self.entry_price:.4f}" # Изменено для ETHUSDT
            })
        except Exception as e:
            print(f"Redis report update error: {e}")

    def session_summary(self):
        """Отправляет итоговый отчет в Redis.""" # <-- МОДИФИЦИРОВАНО
        total_pnl = sum(t['pnl_usdt'] for t in self.trade_history)
        # Отправляем итоговый отчет в специальный ключ
        try:
            r.set(f'bot_summary:{BOT_ID}', f"Final Balance {self.balance_usdt:.2f}, Total PnL {total_pnl:.2f}, Trades {len(self.trade_history)}")
        except Exception as e:
            print(f"Redis summary update error: {e}")
        print(f"Session Summary: Final Balance {self.balance_usdt:.2f}, Total PnL {total_pnl:.2f}, Trades {len(self.trade_history)}")

# =========================================================================
# --- ФУНКЦИИ РАСЧЕТА ИНДИКАТОРОВ (Логика SQZMOM из sqzmom_backtest.py) ---
# =========================================================================

def calculate_atr(df, period):
    """Вычисляет Средний Истинный Диапазон (ATR)."""
    tr = pd.DataFrame({
        'h1': df['High'] - df['Low'],
        'hc': (df['High'] - df['Close'].shift(1)).abs(),
        'lc': (df['Low'] - df['Close'].shift(1)).abs()
    }).max(axis=1)
    df['atr'] = tr.ewm(span=period, adjust=False).mean() 
    return df

def calculate_sqzmom(df, bb_длина, bb_мульти, kc_длина, kc_мульти, atr_период):
    """Вычисляет Squeeze Momentum Indicator."""
    
    # 1. Каналы Келтнера (KC)
    df = calculate_atr(df, atr_период)
    kc_mid = df['Close'].ewm(span=kc_длина, adjust=False).mean()
    kc_верх = kc_mid + (kc_мульти * df['atr'])
    kc_низ = kc_mid - (kc_мульти * df['atr'])
    
    # 2. Полосы Боллинджера (BB)
    bb_mid = df['Close'].rolling(bb_длина).mean()
    stddev = df['Close'].rolling(bb_длина).std()
    bb_верх = bb_mid + (bb_мульти * stddev)
    bb_низ = bb_mid - (bb_мульти * stddev)
    
    # 3. Определение Сжатия (Squeeze)
    df['is_squeeze'] = (bb_верх < kc_верх) & (bb_низ > kc_низ)
    
    # 4. Расчет Импульса (Momentum)
    highest_high = df['High'].rolling(bb_длина).max()
    lowest_low = df['Low'].rolling(bb_длина).min()
    val1 = ((df['Close'] - ((highest_high + lowest_low) / 2)) / (bb_мульти * stddev))
    val2 = val1.ewm(span=kc_длина, adjust=False).mean()
    df['momentum'] = val2 - val2.ewm(span=10, adjust=False).mean() 
    
    return df

# =========================================================================
# --- ДАННЫЕ И СИГНАЛЫ ---
# =========================================================================

@retry_api()
def get_data(symbol, interval, limit=500):
    """Получение исторических данных."""
    klines = client.get_historical_klines(symbol, interval, limit=limit)
    if len(klines) < 200: 
        raise ValueError("Incomplete data")
    df = pd.DataFrame(klines, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'qav', 'trades', 'tbav', 'tqav', 'ignore'])
    df['Close'] = pd.to_numeric(df['Close'])
    df['High'] = pd.to_numeric(df['High'])
    df['Low'] = pd.to_numeric(df['Low'])
    df['Open'] = pd.to_numeric(df['Open'])
    return df

def calculate_indicators(df):
    """Рассчитывает индикатор SQZMOM."""
    df = calculate_sqzmom(
        df, 
        SQZ_BB_ДЛИНА, SQZ_BB_МУЛЬТИФАКТОР, 
        SQZ_KC_ДЛИНА, SQZ_KC_МУЛЬТИФАКТОР,
        SQZ_ATR_ПЕРИОД
    )
    return df

def generate_signals(df):
    """Генерирует сигнал на основе Squeeze Momentum."""
    
    if len(df) < 3:
        return None, None

    prev_row = df.iloc[-2]
    prev_prev_row = df.iloc[-3]
    current_open_price = df['Open'].iloc[-1]
    
    было_сжатие = prev_prev_row['is_squeeze']
    сжатие_закончилось = было_сжатие and not prev_row['is_squeeze']
    
    моментум_положительный = prev_row['momentum'] > 0
    моментум_отрицательный = prev_row['momentum'] < 0

    if сжатие_закончилось and моментум_положительный:
        return 'LONG', current_open_price
    elif сжатие_закончилось and моментум_отрицательный:
        return 'SHORT', current_open_price
        
    return None, None

# =========================================================================
# --- WEBSOCKET ЛОГИКА (Модифицировано для SQZMOM) ---
# =========================================================================

def on_message(ws, message, account):
    try:
        data = json.loads(message)
        # Проверяем, что это сообщение о закрытии свечи на нашем рабочем ТФ
        if data.get('e') == 'kline' and data['k']['x'] and data['k']['i'] == INTERVAL: 
            if not account.session_started:
                return

            # Получаем и рассчитываем данные
            df = get_data(SYMBOL, INTERVAL)
            if df is None:
                return

            df = calculate_indicators(df)

            current_price = float(data['k']['c']) 
            
            signal, entry_price_for_next_candle_raw = generate_signals(df) 

            if account.is_in_position:
                # Проверка SL/TP - используем цену закрытия текущей свечи (current_price)
                if (account.is_long and current_price <= account.stop_loss_level) or \
                   (not account.is_long and current_price >= account.stop_loss_level):
                    account.close_position(account.stop_loss_level, "STOP_LOSS") # Закрытие по цене SL
                elif (account.is_long and current_price >= account.take_profit_level) or \
                     (not account.is_long and current_price <= account.take_profit_level):
                    account.close_position(account.take_profit_level, "TAKE_PROFIT") # Закрытие по цене TP
                # Обратный сигнал не используется в этой версии

            # Вход
            elif signal and entry_price_for_next_candle_raw: 
                
                # Учет Проскальзывания в Цене Входа
                if signal == 'LONG':
                    current_entry_price = entry_price_for_next_candle_raw * (1 + SLIPPAGE_PERCENT)
                else: # SHORT
                    current_entry_price = entry_price_for_next_candle_raw * (1 - SLIPPAGE_PERCENT)
                
                direction = 1 if signal == 'LONG' else -1
                
                # 1. Расчет SL/TP на основе фиксированных процентов
                stop_loss_level = current_entry_price * (1 - direction * RISK_PERCENT_SL)
                take_profit_level = current_entry_price * (1 + direction * PROFIT_PERCENT_TP)
                
                # 2. Расчет расстояния до SL в USD (Риск на 1 монету)
                if signal == 'LONG':
                    price_diff_sl = current_entry_price - stop_loss_level
                else: # SHORT
                    price_diff_sl = stop_loss_level - current_entry_price
                
                # 3. Расчет размера позиции в USDT для входа
                if price_diff_sl <= 0: 
                    print("Error: SL distance is zero or negative.")
                    return
                
                position_size_usdt_entry = (RISK_AMOUNT_USD / price_diff_sl) * current_entry_price
                
                # 4. Проверка лимитов и минимального размера
                if account.check_limits(RISK_AMOUNT_USD) and position_size_usdt_entry >= 10:
                    account.enter_position(current_entry_price, signal == 'LONG', position_size_usdt_entry, stop_loss_level, take_profit_level, margin_usdt=RISK_AMOUNT_USD) 
            
            account.generate_report(current_price)
    except Exception as e:
        print(f"WebSocket message error: {e}")
        # send_telegram_message(f"Error processing WebSocket message: {e}") # УДАЛЕНО

# --- ОСТАЛЬНЫЕ ФУНКЦИИ (Копируем из bot.py) ---

def on_error(ws, error):
    print(f"WebSocket error: {error}")
    # send_telegram_message(f"WebSocket error: {error}") # УДАЛЕНО

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed")
    # send_telegram_message("WebSocket connection closed.") # УДАЛЕНО

def on_open(ws):
    print("WebSocket opened")
    ws.send(json.dumps({
        "method": "SUBSCRIBE",
        "params": [f"{SYMBOL.lower()}@kline_{INTERVAL}"],
        "id": 1
    }))

# --- run_websocket (МОДИФИЦИРОВАНО) ---
def run_websocket(account):
    websocket_url = "wss://stream.binance.com:9443/ws"
    ws = websocket.WebSocketApp(
        websocket_url,
        on_message=lambda ws, msg: on_message(ws, msg, account),
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    ws_thread = threading.Thread(target=ws.run_forever)
    ws_thread.daemon = True
    ws_thread.start()

    # --- ПРОВЕРКА КОМАНДЫ СТОП ЧЕРЕЗ REDIS ---
    while account.session_started:
        time.sleep(1)
        try:
            # Если в Redis есть команда STOP для этого бота, останавливаем
            if r.get(f'command:{BOT_ID}') == 'STOP':
                print(f"Received STOP command from Redis for {BOT_ID}.")
                r.delete(f'command:{BOT_ID}') # Удаляем команду
                account.session_started = False
                break
        except Exception as e:
            print(f"Redis command check error: {e}")
            
        if not ws_thread.is_alive():
            print("WebSocket thread died, restarting...")
            # send_telegram_message("WebSocket connection lost, attempting to restart...") # УДАЛЕНО
            ws_thread = threading.Thread(target=ws.run_forever)
            ws_thread.daemon = True
            ws_thread.start()
    # --- КОНЕЦ ПРОВЕРКИ ---

    # При остановке (либо по команде, либо по MAX_DRAWDOWN)
    if not account.session_started:
        ws.close()
        # При остановке получаем актуальную цену с API для закрытия
        try:
            current_price = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
            if account.is_in_position:
                account.close_position(current_price, "COMMAND_STOP")
            account.session_summary()
        except Exception as e:
            print(f"Error during final closing: {e}")
        
    account.update_redis_status() # Обновить статус в Redis на 'остановлен'

# Функция telegram_bot УДАЛЕНА

# --- ЗАПУСК (МОДИФИЦИРОВАН) ---
def run_bot(bot_id):
    # При запуске проверяем Redis на команду START
    if r.get(f'command:{bot_id}') == 'START':
        r.delete(f'command:{bot_id}')
        
    demo_account = PaperAccount(initial_balance=100.00)
    
    # Основной цикл для ожидания команды START
    while True:
        try:
            if r.get(f'command:{bot_id}') == 'START':
                print(f"Received START command from Redis for {bot_id}. Starting...")
                r.delete(f'command:{bot_id}')
                demo_account.session_started = True
                demo_account.reset_daily()
                # Обновляем статус в Redis на 'запущен'
                demo_account.update_redis_status(is_running=True)
                # Запускаем торговую логику
                run_websocket(demo_account)
                print(f"Bot {bot_id} finished run_websocket, waiting for next START command.")
            
            # Обновление статуса 'ожидает' в Redis
            r.hset(f'bot_status:{bot_id}', mapping={
                'running': 0,
                'in_position': 0,
                'last_update': time.time(),
                'state_message': 'Waiting for START command'
            })
            time.sleep(5) # Ожидание команды START
            
        except Exception as e:
            print(f"Critical error in main loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    print(f"SQZMOM Бот {BOT_ID} запущен, ожидает команды START.")
    # send_telegram_message("SQZMOM Бот запущен, ожидает команды...\n") # УДАЛЕНО
    try:
        run_bot(BOT_ID)
    except Exception as e:
        print(f"Critical error: {e}")
        sys.exit(1)