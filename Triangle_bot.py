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

FEE = 0.001
MIN_PROFIT_PCT = 0.5
MIN_LIQUIDITY = 100
MAX_LIQUIDITY = 500000
TRADE_USD = 100

STABLES = {'USDT', 'USDC', 'DAI', 'USDE', 'USDR', 'TUSD', 'BUSD'}
BASE_COINS = {'BTC', 'ETH', 'BNB', 'SOL'}

def get_symbol_type(symbol):
    if symbol in STABLES:
        return 'stable'
    elif symbol in BASE_COINS:
        return 'base'
    else:
        return 'alt'

bybit = ccxt.bybit({
    'apiKey': API_KEY,
    'secret': SECRET,
    'enableRateLimit': True,
    'timeout': 15000,
    'options': {'defaultType': 'spot'}
})

markets = bybit.load_markets({'type': 'spot'})
all_symbols = list(markets.keys())

unique_symbols = set()
for s in all_symbols:
    if '/' in s:
        base, quote = s.split('/')
        unique_symbols.add(base)
        unique_symbols.add(quote)

symbol_types = {sym: get_symbol_type(sym) for sym in unique_symbols}

base_symbols = set()
for s in all_symbols:
    if any(s.endswith(f'/{stable}') for stable in STABLES):
        base_symbols.add(s.split('/')[0])

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
            if pair3 not in markets:
                continue
            routes.append((pair1, pair2, pair3, invert2))

print(f"\n🔁 Всего маршрутов найдено: {len(routes)}")

def get_orderbook(symbol):
    try:
        return bybit.fetch_order_book(symbol)
    except:
        return {'asks': [], 'bids': []}
     
def get_best_price(book, side, amount_needed):
    """
    Вычисляет средневзвешенную цену по стакану, чтобы набрать нужную сумму
    :param book: словарь {'asks': [...], 'bids': [...]}
    :param side: 'asks' или 'bids'
    :param amount_needed: сколько монеты или USDT нужно купить/продать
    :return: (средняя цена, итоговая стоимость)
    """
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

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'})

def get_avg_price_and_qty(levels, usd_amount):
    total_cost = 0
    total_qty = 0
    for price, amount in levels:
        if price <= 0 or price > 1e6:
            continue
        cost = price * amount
        if total_cost + cost >= usd_amount:
            needed = (usd_amount - total_cost) / price
            total_qty += needed
            total_cost += needed * price
            break
        total_cost += cost
        total_qty += amount
    if total_qty == 0:
        return None, 0, 0
    avg_price = total_cost / total_qty
    if avg_price <= 0 or avg_price > 1e6:
        return None, 0, 0
    return avg_price, total_qty, total_cost

def calc_triangle(p1, p2, p3, invert2):
    base1 = p1.split('/')[0]  # монета A
    quote1 = p1.split('/')[1]  # USDT
    coinA = base1
    coinB = p2.split('/')[0] if not invert2 else p2.split('/')[1]  # монета B

    typeA = symbol_types.get(coinA, 'alt')
    typeB = symbol_types.get(coinB, 'alt')

    # Шаг 1. Купить A за USDT (ask стакан)
    book1 = get_orderbook(p1)
    p1_price, spent_usdt = get_best_price(book1, 'asks', TRADE_USD)
    if not p1_price:
        return
    amount_a = TRADE_USD / p1_price * (1 - FEE)
    liq1 = book1['asks'][0][0] * book1['asks'][0][1]

    # Шаг 2. Обмен A на B
    book2 = get_orderbook(p2)
    if not book2['asks'] or not book2['bids']:
        return

    # Определение стороны стакана по логике:
    # 1. Если A — stable ⇒ покупаем B (по asks)
    # 2. Если A — base или alt, B — stable ⇒ продаём A (по bids)
    # 3. Если A и B — alt/base ⇒ покупаем B (по asks)
    if typeA == 'stable':
        p2_price, _ = get_best_price(book2, 'asks', amount_a)
        if not p2_price:
            return
        amount_b = amount_a / p2_price if invert2 else amount_a * p2_price
        amount_b *= (1 - FEE)
        liq2 = book2['asks'][0][0] * book2['asks'][0][1]

    elif typeB == 'stable':
        p2_price, _ = get_best_price(book2, 'bids', amount_a)
        if not p2_price:
            return
        amount_b = amount_a * p2_price if invert2 else amount_a / p2_price
        amount_b *= (1 - FEE)
        liq2 = book2['bids'][0][0] * book2['bids'][0][1]

    else:
        p2_price, _ = get_best_price(book2, 'asks', amount_a)
        if not p2_price:
            return
        amount_b = amount_a / p2_price if invert2 else amount_a * p2_price
        amount_b *= (1 - FEE)
        liq2 = book2['asks'][0][0] * book2['asks'][0][1]

    # Шаг 3. Продать B за USDT (bid стакан)
    book3 = get_orderbook(p3)
    p3_price, total_usdt = get_best_price(book3, 'bids', amount_b)
    if not p3_price:
        return
    total_usdt = amount_b * p3_price * (1 - FEE)
    liq3 = book3['bids'][0][0] * book3['bids'][0][1]

    # Проверка ликвидности
    min_liq = min(liq1, liq2, liq3)
    if min_liq < MIN_LIQUIDITY or min_liq > MAX_LIQUIDITY:
        return

    # Проверка адекватности финального значения
    if total_usdt <= 0 or total_usdt > 10 * TRADE_USD:
        return

    profit = total_usdt - TRADE_USD
    pct = (profit / TRADE_USD) * 100

    if pct >= MIN_PROFIT_PCT:
        now = datetime.now().strftime('%H:%M:%S')
        msg = (
            f"<b>[{now}]</b>\n"
            f"🟥 1. {p1} - {p1_price:.6f}, стакан: ${liq1:,.0f}\n"
            f"{'🟥' if typeA == 'stable' else '🟢'} 2. {p2} - {p2_price:.6f}, стакан: ${liq2:,.0f}\n"
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
        for i, (p1, p2, p3, invert2) in enumerate(routes):
            print(f"🔍 [{i+1}/{len(routes)}] {p1} → {p2} → {p3}")
            res = calc_triangle(p1, p2, p3, invert2)
            if res:
                profit, pct, vol = res
                results.append((p1, p2, p3, profit, pct, vol))
            time.sleep(0.1)

        results = [r for r in results if 0 < r[4] < 10]
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