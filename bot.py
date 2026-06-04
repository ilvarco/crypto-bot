import time, hmac, hashlib, requests
from datetime import datetime
from math import floor

API_KEY = open('/root/apikey.txt').read().strip().split('\n')[0]
SECRET_KEY = open('/root/apikey.txt').read().strip().split('\n')[1]
THRESHOLD = 0.3
START_COIN = 'SOL'
COMMISSION = 0.15
COINS = ['BTC', 'ETH', 'SOL', 'BNB']
PAIRS = {'BTC': 'BTCUSDT', 'ETH': 'ETHUSDT', 'SOL': 'SOLUSDT', 'BNB': 'BNBUSDT'}
BASE_URL = 'https://api.binance.com'
current_coin = START_COIN
base_prices = {}
total_gain = 0.0
lot_sizes = {}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def sign(params):
    query = '&'.join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + '&signature=' + sig

def get_time():
    return requests.get(f"{BASE_URL}/api/v3/time").json()['serverTime']

def get_lot_size(symbol):
    if symbol in lot_sizes:
        return lot_sizes[symbol]
    r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", params={'symbol': symbol})
    for f in r.json()['symbols'][0]['filters']:
        if f['filterType'] == 'LOT_SIZE':
            step = float(f['stepSize'])
            lot_sizes[symbol] = step
            return step
    return 0.001

def round_step(qty, step):
    precision = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
    return round(floor(qty / step) * step, precision)

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
    if 'balances' not in data:
        raise Exception(f"Error balances: {data}")
    return {b['asset']: float(b['free']) for b in data['balances'] if b['asset'] in COINS}

def order(symbol, side, qty):
    step = get_lot_size(symbol)
    qty = round_step(qty, step)
    ts = get_time()
    signed = sign({'symbol': symbol, 'side': side, 'type': 'MARKET', 'quantity': qty, 'timestamp': ts})
    r = requests.post(f"{BASE_URL}/api/v3/order", headers={'X-MBX-APIKEY': API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'}, data=signed)
    data = r.json()
    if 'code' in data:
        raise Exception(data['msg'])
    return data

def swap(fc, tc, balances, prices):
    sq = balances[fc] *
