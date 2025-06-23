import ccxt
import time
import requests
import os
from datetime import datetime

# === Константы и настройки ===
FEE = 0.001
MIN_PROFIT_PCT = 0.5
MIN_LIQUIDITY = 100
MAX_LIQUIDITY = 500000
TRADE_USD = 100

# === Переменные окружения (Railway .env) ===
API_KEY = os.environ.get('API_KEY')
SECRET = os.environ.get('SECRET')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Проверка ключей
if not API_KEY or not SECRET:
    raise Exception("❌ API_KEY и SECRET обязательны. Установи их в Railway → Variables")

# === Типы монет ===
STABLES = {'USDT', 'USDC', 'DAI', 'USDE', 'USDR', 'TUSD', 'BUSD'}
BASE_COINS = {'BTC', 'ETH', 'BNB', 'SOL'}

def get_symbol_type(symbol):
    if symbol in STABLES:
        return 'stable'
    elif symbol in BASE_COINS:
        return 'base'
    else:
        return 'alt'

# === Telegram ===
def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'})
    except:
        pass

# === Инициализация Bybit ===
bybit = ccxt.bybit({
    'apiKey': API_KEY,
    'secret': SECRET,
    'enableRateLimit': True,
    'timeout': 15000,
    'options': {'defaultType': 'spot'}
})

# === Получение рынков и маршрутов ===
markets = bybit.load_markets({'type': 'spot'})
all_symbols = list(markets.keys())

unique_symbols = set()
for s in all_symbols:
    if '/' in s:
        base, quote = s.split('/')
        unique_symbols.add(base)
        unique_symbols.add(quote)

symbol_types = {sym: get_symbol_type(sym) for sym in unique_symbols}

base_symbols = {s.split('/')[0] for s in all_symbols if any(s.endswith(f'/{sfx}') for sfx in STABLES)}

routes = []
for s in STABLES:
    for a in base_symbols:
        for b in base_symbols:
            if a == b:
                continue
            pair1 = f"{a}/{s}"
            if pair1 not in markets:
                continue
            if f"{b}/{a}" in markets:
                pair2 = f"{b}/{a}"
                invert2 = True
            elif f"{a}/{b}" in markets:
                pair2 = f"{a}/{b}"
                invert2 = False
            else:
                continue
            pair3 = f"{b}/{s}"
            if pair3 in markets:
                routes.append((pair1, pair2, pair3, invert2))

print(f"🔁 Всего маршрутов найдено: {len(routes)}")

# === Стаканы и цены ===
def get_orderbook(symbol):
    try:
        return bybit.fetch_order_book(symbol)
    except:
        return {'asks': [], 'bids': []}

def get_best_price(book, side, amount_needed):
    total = 0
    qty = 0
    for price, volume in book[side]:
        deal = price * volume
        if total + deal >= amount_needed:
            partial = (amount_needed - total) / price
            qty += partial
            total += partial * price
            break
        total += deal
        qty += volume
    if qty == 0:
        return None, 0
    avg_price = total / qty
    return avg_price, total

# === Основная логика треугольника ===
def calc_triangle(p1, p2, p3, invert2):
    coinA = p1.split('/')[0]
    coinB = p2.split('/')[1] if invert2 else p2.split('/')[0]

    typeA = symbol_types.get(coinA, 'alt')
    typeB = symbol_types.get(coinB, 'alt')

    book1 = get_orderbook(p1)
    price1, _ = get_best_price(book1, 'asks', TRADE_USD)
    if not price1:
        return
    amount_a = TRADE_USD / price1 * (1 - FEE)
    liq1 = book1['asks'][0][0] * book1['asks'][0][1]

    book2 = get_orderbook(p2)
    if not book2['asks'] or not book2['bids']:
        return

    if typeA == 'stable':
        side2 = 'asks'
    elif typeB == 'stable':
        side2 = 'bids'
    else:
        side2 = 'asks'

    price2, _ = get_best_price(book2, side2, amount_a)
    if not price2:
        return

    amount_b = (
        (amount_a / price2 if invert2 else amount_a * price2)
        if side2 == 'asks' else
        (amount_a * price2 if invert2 else amount_a / price2)
    ) * (1 - FEE)

    liq2 = book2[side2][0][0] * book2[side2][0][1]

    book3 = get_orderbook(p3)
    price3, _ = get_best_price(book3, 'bids', amount_b)
    if not price3:
        return
    total_usdt = amount_b * price3 * (1 - FEE)
    liq3 = book3['bids'][0][0] * book3['bids'][0][1]

    min_liq = min(liq1, liq2, liq3)
    if min_liq < MIN_LIQUIDITY or min_liq > MAX_LIQUIDITY:
        return

    if total_usdt <= 0 or total_usdt > TRADE_USD * 10:
        return

    profit = total_usdt - TRADE_USD
    pct = (profit / TRADE_USD) * 100

    if pct >= MIN_PROFIT_PCT:
        now = datetime.now().strftime('%H:%M:%S')
        msg = (
            f"<b>[{now}]</b>\n"
            f"🟥 1. {p1} - {price1:.6f}, стакан: ${liq1:,.0f}\n"
            f"{'🟥' if typeA == 'stable' else '🟢'} 2. {p2} - {price2:.6f}, стакан: ${liq2:,.0f}\n"
            f"🟢 3. {p3} - {price3:.6f}, стакан: ${liq3:,.0f}\n\n"
            f"💰 Прибыль: <b>{profit:.2f} USDT</b>\n"
            f"📈 Спред: <b>{pct:.2f}%</b>\n"
            f"💧 Ликвидность: <b>{min_liq:,.0f} USDT</b>"
        )
        send_telegram_message(msg)

    return profit, pct, min_liq

# === Цикл ===
def main():
    while True:
        results = []
        for i, (p1, p2, p3, invert2) in enumerate(routes):
            print(f"🔍 [{i+1}/{len(routes)}] {p1} → {p2} → {p3}")
            try:
                res = calc_triangle(p1, p2, p3, invert2)
                if res:
                    results.append((p1, p2, p3, *res))
            except Exception as e:
                print(f"⚠️ Ошибка: {e}")
            time.sleep(0.1)

        results = [r for r in results if 0 < r[4] < 10]
        results.sort(key=lambda x: x[4], reverse=True)

        print("\033c")  # очистка экрана
        print("📈 ТОП-10 прибыльных маршрутов:")
        for i, (a, b, c, profit, pct, vol) in enumerate(results[:10]):
            print(f"{i+1}. {a} → {b} → {c}")
            print(f"   🔹 Прибыль: {profit:.4f} USDT | Спред: {pct:.2f}% | Ликвидность: {vol:,.0f} USDT\n")

        print("♻️ Обновление через 10 секунд...")
        time.sleep(10)

if __name__ == '__main__':
    main()