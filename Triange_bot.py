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

# === Классификация типов монет ===
STABLES = {'USDT', 'USDC', 'DAI', 'USDE', 'USDR', 'TUSD', 'BUSD'}
BASE_COINS = {'BTC', 'ETH', 'BNB', 'SOL'}

def get_symbol_type(symbol):
    if symbol in STABLES:
        return 'stable'
    elif symbol in BASE_COINS:
        return 'base'
    else:
        return 'alt'

unique_symbols = set()
for s in all_symbols:
    if '/' in s:
        base, quote = s.split('/')
        unique_symbols.add(base)
        unique_symbols.add(quote)

symbol_types = {sym: get_symbol_type(sym) for sym in unique_symbols}

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
    except Exception:
        return {'asks': [], 'bids': []}


def send_telegram_message(text):
    if 'DAI' in text:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'})
    except Exception:
        pass


def get_best_price(book, side, usd_amount):
    total = 0
    qty = 0
    for price, amount in book.get(side, []):
        cost = price * amount
        if total + cost >= usd_amount:
            needed = (usd_amount - total) / price
            qty += needed
            total += needed * price
            break
        total += cost
        qty += amount
    if qty == 0:
        return None, 0
    avg_price = total / qty
    return avg_price, total


def calc_triangle(p1, p2, p3, invert2):
    # Извлечь символы из пар
    m1 = p1.split('/')[0]  # Монета1
    m2 = p2.split('/')[0] if not invert2 else p2.split('/')[1]  # Монета2

    # Определяем типы монет
    type_m1 = symbol_types.get(m1, 'alt')
    type_m2 = symbol_types.get(m2, 'alt')

    book1 = get_orderbook(p1)
    book2 = get_orderbook(p2)
    book3 = get_orderbook(p3)

    if not book1 or not book2 or not book3:
        return

    # 1. Покупка Монета1 за USDT (по ask)
    p1_price, a_cost = get_best_price({'asks': book1.get('asks', [])}, 'asks', TRADE_USD)
    if not p1_price:
        return
    amount1 = TRADE_USD / p1_price * (1 - FEE)
    liq1 = book1['asks'][0][0] * book1['asks'][0][1]

    # 2. Логика для 2-й пары в соответствии с твоей логикой

    # Если Монета1 — стейблкоин, покупаем Монета2 за стейбл (смотрим asks)
    if type_m1 == 'stable':
        p2_price, _ = get_best_price(book2, 'asks', amount1)
        if not p2_price:
            return
        if invert2:
            amount2 = amount1 / p2_price * (1 - FEE)
        else:
            amount2 = amount1 * p2_price * (1 - FEE)
        liq2 = book2['asks'][0][0] * book2['asks'][0][1]

    # Если Монета1 — базовый/альткоин, а Монета2 — стейблкоин, продаём Монета1 за Монета2 (смотрим bids)
    elif type_m1 in ('base', 'alt') and type_m2 == 'stable':
        p2_price, _ = get_best_price(book2, 'bids', amount1)
        if not p2_price:
            return
        if invert2:
            amount2 = amount1 * p2_price * (1 - FEE)
        else:
            amount2 = amount1 / p2_price * (1 - FEE)
        liq2 = book2['bids'][0][0] * book2['bids'][0][1]

    # Если Монета1 — базовый/альткоин, а Монета2 — альткоин, покупаем Монета2 за Монета1 (смотрим asks)
    elif type_m1 in ('base', 'alt') and type_m2 == 'alt':
        p2_price, _ = get_best_price(book2, 'asks', amount1)
        if not p2_price:
            return
        if invert2:
            amount2 = amount1 / p2_price * (1 - FEE)
        else:
            amount2 = amount1 * p2_price * (1 - FEE)
        liq2 = book2['asks'][0][0] * book2['asks'][0][1]

    else:
        return

    # 3. Продажа Монета2 за USDT (по bid)
    p3_price, _ = get_best_price({'bids': book3.get('bids', [])}, 'bids', amount2 * book3['bids'][0][0])
    if not p3_price:
        return
    final_usd = amount2 * p3_price * (1 - FEE)
    liq3 = book3['bids'][0][0] * book3['bids'][0][1]

    # Проверка ликвидности
    min_liq = min(liq1, liq2, liq3)
    if not (MIN_LIQUIDITY <= min_liq <= MAX_LIQUIDITY):
        return

    profit = final_usd - TRADE_USD
    pct = (profit / TRADE_USD) * 100

    if pct >= MIN_PROFIT_PCT:
        now = datetime.now().strftime('%H:%M:%S')
        msg = (
            f"<b>[{now}]</b>\n"
            f"🟥 1. {p1} - {p1_price:.6f}, стакан: ${liq1:,.0f}\n"
            f"{'🟥' if type_m1 == 'stable' or (type_m1 in ('base','alt') and type_m2 == 'alt') else '🟢'} 2. {p2} - {p2_price:.6f}, стакан: ${liq2:,.0f}\n"
            f"🟢 3. {p3} - {p3_price:.6f}, стакан: ${liq3:,.0f}\n\n"
            f"💰 Прибыль: <b>{profit:.2f} USDT</b>\n"
            f"📈 Спред: <b>{pct:.2f}%</b>\n"
            f"💧 Ликвидность: <b>{min_liq:,.0f} USDT</b>"
        )
        send_telegram_message(msg)

    return profit, pct, min_liq


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