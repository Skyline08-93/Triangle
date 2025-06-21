import ccxt
import time
import requests
import os
from colorama import Fore, init
init(autoreset=True)

# 📦 Переменные из Railway окружения
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

markets = bybit.load_markets({
    'type': 'spot'
})

FEE = 0.001
MIN_PROFIT_PCT = 0.5
MIN_LIQUIDITY = 100

markets = bybit.load_markets()
all_symbols = list(markets.keys())
usdt_pairs = [s for s in all_symbols if s.endswith('/USDT') and markets[s]['active']]
base_symbols = [s.replace('/USDT', '') for s in usdt_pairs]

routes = []
for a in base_symbols:
    for b in base_symbols:
        if a == b:
            continue
        pair1 = f"{a}/USDT"
        if f"{a}/{b}" in markets:
            pair2 = f"{a}/{b}"
            invert2 = False
        elif f"{b}/{a}" in markets:
            pair2 = f"{b}/{a}"
            invert2 = True
        else:
            continue
        pair3 = f"{b}/USDT"
        if pair1 in markets and pair3 in markets:
            routes.append((pair1, pair2, pair3, invert2))

print(f"🔄 Найдено маршрутов: {len(routes)}\n")

def get_orderbook(symbol):
    try:
        book = bybit.fetch_order_book(symbol)
        ask = book['asks'][0] if book['asks'] else [None, 0]
        bid = book['bids'][0] if book['bids'] else [None, 0]
        return ask, bid, book
    except Exception as e:
        print(f"Ошибка стакана {symbol}: {e}")
        return [None, 0], [None, 0], {'asks': [], 'bids': []}

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}
    try:
        requests.post(url, data=payload)
    except:
        print("❌ Не удалось отправить сообщение в Telegram")

def calc_triangle(p1, p2, p3, invert2):
    a_ask, _, book1 = get_orderbook(p1)
    _, ab_bid, book2 = get_orderbook(p2)
    _, b_usdt_bid, book3 = get_orderbook(p3)

    if not all([a_ask[0], ab_bid[0], b_usdt_bid[0]]):
        return None

    try:
        if a_ask[0] <= 0 or ab_bid[0] <= 0 or b_usdt_bid[0] <= 0:
            return None

        vol1 = a_ask[0] * a_ask[1]
        vol2 = ab_bid[0] * ab_bid[1]
        vol3 = b_usdt_bid[0] * b_usdt_bid[1]
        min_vol = min(vol1, vol2, vol3)
        if min_vol < MIN_LIQUIDITY:
            return None

        amount = 100
        step1 = amount / a_ask[0] * (1 - FEE)
        if invert2:
            step2 = step1 / ab_bid[0] * (1 - FEE)
        else:
            step2 = step1 * ab_bid[0] * (1 - FEE)
        final = step2 * b_usdt_bid[0] * (1 - FEE)

        profit = final - amount
        pct = (profit / amount) * 100

        if pct >= MIN_PROFIT_PCT:
            msg = f"🚨 <b>АРБИТРАЖ НА BYBIT</b>\n{p1} → {p2} → {p3}\n💰 Прибыль: <b>{profit:.2f} USDT</b>\n📈 Спред: {pct:.2f}%\n💧 Ликвидность: {min_vol:,.0f} USDT"
            send_telegram_message(msg)

        return profit, pct, min_vol
    except:
        return None

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