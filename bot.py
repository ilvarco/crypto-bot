import time, hmac, hashlib, requests
from datetime import datetime

API_KEY = open('/root/apikey.txt').read().strip().split('\n')[0]
SECRET_KEY = open('/root/apikey.txt').read().strip().split('\n')[1]
THRESHOLD = 0.5
START_COIN = 'BNB'
COMMISSION = 0.15
COINS = ['BTC', 'ETH', 'SOL', 'BNB']
PAIRS = {'BTC': 'BTCUSDT', 'ETH': 'ETHUSDT', 'SOL': 'SOLUSDT', 'BNB': 'BNBUSDT'}
BASE_URL = 'https://api.binance.com'
current_coin = START_COIN
base_prices = {}
total_gain = 0.0

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def sign(params):
    query = '&'.join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + '&signature=' + sig

def get_time():
    return requests.get(f"{BASE_URL}/api/v3/time").json()['serverTime']

def get_prices():
    prices = {}
    for coin in COINS:
        try:
            r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={'symbol': PAIRS[coin]}, timeout=10)
            data = r.json()
            if 'price' in data:
                prices[coin] = float(data['price'])
        except Exception as e:
            log(f"Error precio {coin}: {e}")
    return prices

def get_balances():
    ts = get_time()
    signed = sign({'timestamp': ts})
    r = requests.get(f"{BASE_URL}/api/v3/account?{signed}", headers={'X-MBX-APIKEY': API_KEY})
    data = r.json()
    log(f"Balances: {str(data)[:300]}")
    if 'balances' not in data:
        raise Exception(f"Error balances: {data}")
    return {b['asset']: float(b['free']) for b in data['balances'] if b['asset'] in COINS}

def order(symbol, side, qty):
    ts = get_time()
    signed = sign({'symbol': symbol, 'side': side, 'type': 'MARKET', 'quantity': round(qty, 6), 'timestamp': ts})
    r = requests.post(f"{BASE_URL}/api/v3/order", headers={'X-MBX-APIKEY': API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}, data=signed)
    data = r.json()
    if 'code' in data:
        raise Exception(data['msg'])
    return data

def swap(fc, tc, balances, prices):
    sq = round(balances[fc] * 0.999, 6)
    log(f"  Vendiendo {sq} {fc}")
    res = order(fc + 'USDT', 'SELL', sq)
    usdt = float(res['cummulativeQuoteQty']) * 0.999
    time.sleep(1.5)
    bq = round(usdt / prices[tc] * 0.999, 6)
    log(f"  Comprando {bq} {tc}")
    order(tc + 'USDT', 'BUY', bq)

def cycle():
    global current_coin, base_prices, total_gain
    prices = get_prices()
    for c in COINS:
        if c not in base_prices and c in prices:
            base_prices[c] = prices[c]
    if len(prices) < 4:
        return
    pcts = {c: (prices[c] - base_prices[c]) / base_prices[c] * 100 for c in COINS if c in prices and c in base_prices}
    best = min(pcts, key=pcts.get)
    diff = abs(pcts[best] - pcts[current_coin])
    log(f"{current_coin}({pcts[current_coin]:.2f}%) -> mejor:{best}({pcts[best]:.2f}%) diff:{diff:.2f}%")
    if best != current_coin and diff >= THRESHOLD:
        balances = get_balances()
        swap(current_coin, best, balances, prices)
        total_gain += diff - COMMISSION
        current_coin = best
        log(f"OK rotacion | acum: +{total_gain:.2f}%")

log("BOT INICIADO")
while True:
    try:
        cycle()
    except Exception as e:
        log(f"ERROR: {e}")
    time.sleep(30)
