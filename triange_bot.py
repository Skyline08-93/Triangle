import ccxt
import time
import requests
from datetime import datetime
from colorama import Fore, init

init(autoreset=True)

API_KEY = 'GenOyt0Rf02LombN6e'
SECRET = 'vxOegi3zL11hcvWOy2DFUNxV4mO8S8KFPtsX'
TELEGRAM_TOKEN = '7567914583:AAF7G3rWkc3K5pbIBV8pUadjxdG98mra4F4'
CHAT_ID = '937242089'

bybit = ccxt.bybit({
    'apiKey': API_KEY,
    'secret': SECRET,
    'enableRateLimit': True,
    'timeout': 10000,
    'options': {'defaultType': 'spot'}
})

try:
    markets = bybit.load_markets({'type': 'spot'})
except Exception as e:
    print(f"Ошибка загрузки рынков: {e}")
    exit()

all_symbols = list(markets.keys())
usdt_pairs = [s for s in all_symbols if s.endswith('/USDT') and markets[s]['active']]
base_symbols = [s.replace('/USDT', '') for s in usdt_pairs]

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

all_coins_for_m2 = list(set(base_symbols) | STABLES)

routes = []
for coin1 in base_symbols:
    pair1 = f"{coin1}/USDT"
    if pair1 not in markets:
        continue
    for coin2 in all_coins_for_m2:
        if coin2 == coin1:
            continue
        pair2 = f"{coin2}/{coin1}"
        pair3 = f"{coin2}/USDT"
        if pair2 in markets and pair3 in markets:
            routes.append((pair1, pair2, pair3, False))

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
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

def get_avg_price_and_qty(book_side, amount_in_quote):
    total_quote = 0
    total_qty = 0
    for price, qty in book_side:
        cost = price * qty
        if total_quote + cost >= amount_in_quote:
            needed_qty = (amount_in_quote - total_quote) / price
            total_qty += needed_qty
            total_quote += needed_qty * price
            break
        total_qty += qty
        total_quote += cost
    if total_qty == 0:
        return None, 0, 0
    avg_price = total_quote / total_qty
    return avg_price, total_qty, total_quote

def calc_triangle(p1, p2, p3, invert2):
    m1 = p1.split('/')[0]
    m2 = p2.split('/')[0] if not invert2 else p2.split('/')[1]

    type_m1 = symbol_types.get(m1, 'alt')
    type_m2 = symbol_types.get(m2, 'alt')

    book1 = get_orderbook(p1)
    book2 = get_orderbook(p2)
    book3 = get_orderbook(p3)

    if not book1 or not book2 or not book3:
        return

    # 1. Покупаем m1 за USDT (asks)
    asks1 = book1.get('asks', [])
    if not asks1:
        return
    p1_price, qty1, spent_usd = get_avg_price_and_qty(asks1, TRADE_USD)
    if not p1_price:
        return
    qty1 *= (1 - FEE)
    liq1 = asks1[0][0] * asks1[0][1]

    # 2. Торгуем m2/m1
    if type_m1 == 'stable':
        asks2 = book2.get('asks', [])
        if not asks2:
            return
        p2_price, qty2, spent_m1 = get_avg_price_and_qty(asks2, qty1)
        if not p2_price:
            return
        qty2 *= (1 - FEE)
        liq2 = asks2[0][0] * asks2[0][1]
    elif type_m1 in ('base', 'alt') and type_m2 == 'stable':
        bids2 = book2.get('bids', [])
        if not bids2:
            return
        p2_price, qty2, gained_m2 = get_avg_price_and_qty(bids2, qty1)
        if not p2_price:
            return
        qty2 *= (1 - FEE)
        liq2 = bids2[0][0] * bids2[0][1]
    elif type_m1 in ('base', 'alt') and type_m2 == 'alt':
        asks2 = book2.get('asks', [])
        if not asks2:
            return
        p2_price, qty2, spent_m1 = get_avg_price_and_qty(asks2, qty1)
        if not p2_price:
            return
        qty2 *= (1 - FEE)
        liq2 = asks2[0][0] * asks2[0][1]
    else:
        return

    # 3. Продаём m2 за USDT (bids)
    bids3 = book3.get('bids', [])
    if not bids3:
        return
    p3_price = bids3[0][0]
    if not p3_price:
        return
    final_usdt = qty2 * p3_price * (1 - FEE)
    liq3 = bids3[0][0] * bids3[0][1]

    min_liq = min(liq1, liq2, liq3)
    if not (MIN_LIQUIDITY <= min_liq <= MAX_LIQUIDITY):
        return

    profit = final_usdt - TRADE_USD
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