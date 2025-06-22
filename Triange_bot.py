import ccxt
import time
import requests
import os
from datetime import datetime
from colorama import Fore, init
init(autoreset=True)

API_KEY = os.getenv('API_KEY')
SECRET = os.getenv('SECRET')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

bybit = ccxt.bybit({
    'apiKey': API_KEY,
    'secret': SECRET,
    'enableRateLimit': True,
    'timeout': 15000,
    'options': {'defaultType': 'spot'}
})

markets = bybit.load_markets({'type': 'spot'})
all_symbols = list(markets.keys())
usdt_pairs = [s for s in all_symbols if s.endswith('/USDT') and markets[s]['active']]
base_symbols = [s.replace('/USDT', '') for s in usdt_pairs]

FEE = 0.001
MIN_PROFIT_PCT = 0.5
MIN_LIQUIDITY = 100
MAX_LIQUIDITY = 500000
TRADE_USD = 100

routes = []
for a in base_symbols:
    for b in base_symbols:
        if a == b:
            continue
        pair1 = f"{a}/USDT"
        if f"{b}/{a}" in markets:
            pair2 = f"{b}/{a}"
            invert2 = True
        elif f"{a}/{b}" in markets:
            pair2 = f"{a}/{b}"
            invert2 = False
        else:
            continue
        pair3 = f"{b}/USDT"
        if pair1 in markets and pair3 in markets:
            routes.append((pair1, pair2, pair3, invert2))

print(f"🔄 Найдено маршрутов: {len(routes)}\n")

def get_orderbook(symbol):
    try:
        return bybit.fetch_order_book(symbol)
    except Exception as e:
        print(f"Ошибка стакана {symbol}: {e}")
        return {'asks': [], 'bids': []}

def send_telegram_message(text):
    if 'DAI' in text:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}
    try:
        requests.post(url, data=payload)
    except:
        print("❌ Не удалось отправить сообщение в Telegram")

def calc_triangle(p1, p2, p3, invert2):
    book1 = get_orderbook(p1)
    book2 = get_orderbook(p2)
    book3 = get_orderbook(p3)

    if not book1['asks'] or not book2['asks'] or not book3['bids']:
        return None

    # ЛОГИКА:
    # 🔴 1. Покупаем base1 за USDT → ask из p1
    a_price = book1['asks'][0][0]
    a_qty = book1['asks'][0][1]
    a_cost = TRADE_USD
    a_amount = a_cost / a_price * (1 - FEE)

    # 🔴 2. Покупаем base2 за base1 → ask из p2
    b_price = book2['asks'][0][0]
    b_qty = book2['asks'][0][1]
    if invert2:
        b_amount = a_amount / b_price * (1 - FEE)
        b_cost = a_amount
    else:
        b_amount = a_amount * b_price * (1 - FEE)
        b_cost = a_amount * b_price

    # 🟢 3. Продаём base2 за USDT → bid из p3
    c_price = book3['bids'][0][0]
    c_qty = book3['bids'][0][1]
    c_cost = b_amount * c_price * (1 - FEE)

    a_liq = a_price * a_qty
    b_liq = b_price * b_qty
    c_liq = c_price * c_qty
    min_vol = min(a_liq, b_liq, c_liq)

    if not (MIN_LIQUIDITY <= min_vol <= MAX_LIQUIDITY):
        return None

    profit = c_cost - TRADE_USD
    pct = (profit / TRADE_USD) * 100

    if pct >= MIN_PROFIT_PCT:
        now = datetime.now().strftime('%H:%M:%S')
        msg = (
            f"<b>[{now}]</b>\n"
            f"🔴1. {p1} - {a_price:.6f}, ${a_price * a_qty:,.0f}\n"
            f"🔴2. {p2} - {b_price:.6f}, ${b_price * b_qty:,.0f}\n"
            f"🟢3. {p3} - {c_price:.6f}, ${c_price * c_qty:,.0f}\n\n"
            f"💰 Прибыль: <b>{profit:.2f} USDT</b>\n"
            f"📈 Спред: <b>{pct:.2f}%</b>\n"
            f"💧 Ликвидность: <b>{min_vol:,.0f} USDT</b>"
        )
        send_telegram_message(msg)

    return profit, pct, min_vol

def run():
    while True:
        results = []
        for i, (p1, p2, p3, invert2) in enumerate(routes[:300]):
            print(f"🔍 [{i+1}/{min(len(routes),300)}] {p1} → {p2} → {p3}")
            res = calc_triangle(p1, p2, p3, invert2)
            if res:
                profit, pct, vol = res
                results.append((p1, p2, p3, profit, pct, vol))
            time.sleep(0.1)

        results = [r for r in results if r[4] > 0]
        results.sort(key=lambda x: x[4], reverse=True)

        print("\033c")
        print("📈 ТОП-10 прибыльных маршрутов:")
        for i, (a, b, c, profit, pct, vol) in enumerate(results[:10]):
            color = Fore.GREEN if pct >= MIN_PROFIT_PCT else Fore.YELLOW
            print(color + f"{i+1}. {a} → {b} → {c}")
            print(color + f"   🔹 Чистая прибыль: {profit:.4f} USDT | Спред: {pct:.2f}% | Ликвидность: {vol:,.0f} USDT\n")

        print("♻️ Обновление через 10 секунд...")
        time.sleep(10)

if __name__ == '__main__':
    run()