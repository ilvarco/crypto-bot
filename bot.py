import os
import time
import hmac
import hashlib
import requests
import json
from datetime import datetime

# ── CONFIGURACIÓN ──
API_KEY    = os.environ.get('BINANCE_API_KEY', '')
SECRET_KEY = os.environ.get('BINANCE_SECRET_KEY', '')
THRESHOLD  = float(os.environ.get('BOT_THRESHOLD', '0.5'))
START_COIN = os.environ.get('START_COIN', 'BNB')
COMMISSION = 0.15  # % comisión + slippage por operación
INTERVAL   = 30    # segundos entre ciclos

COINS = ['BTC', 'ETH', 'SOL', 'BNB']
PAIRS = {'BTC': 'BTCUSDT', 'ETH': 'ETHUSDT', 'SOL': 'SOLUSDT', 'BNB': 'BNBUSDT'}
BASE_URL = 'https://api.binance.com'

# ── ESTADO ──
current_coin = START_COIN
base_prices  = {}
total_gain   = 0.0
ops          = []

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def sign(params: dict) -> str:
    query = '&'.join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + '&signature=' + signature

def get_server_time() -> int:
    r = requests.get(f"{BASE_URL}/api/v3/time")
    return r.json()['serverTime']

def get_prices() -> dict:
    prices = {}
    for coin in COINS:
        try:
            r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={'symbol': PAIRS[coin]}, timeout=10)
            log(f"API {coin} status:{r.status_code} resp:{r.text[:100]}")
            data = r.json()
            if isinstance(data, dict) and 'price' in data:
                prices[coin] = float(data['price'])
            elif isinstance(data, list) and len(data) > 0:
                prices[coin] = float(data[0]['price'])
            else:
                log(f"Respuesta inesperada para {coin}: {data}")
        except Exception as e:
            log(f"Error obteniendo precio de {coin}: {e}")
    return prices

def get_balances() -> dict:
    ts = get_server_time()
    params = {'timestamp': ts}
    signed = sign(params)
    r = requests.get(
        f"{BASE_URL}/api/v3/account?{signed}",
        headers={'X-MBX-APIKEY': API_KEY}
    )
    data = r.json()
    if 'balances' not in data:
        raise Exception(f"Error API: {data.get('msg', 'desconocido')}")
    return {b['asset']: float(b['free']) for b in data['balances'] if b['asset'] in COINS}

def execute_order(symbol: str, side: str, quantity: float):
    ts = get_server_time()
    params = {
        'symbol': symbol,
        'side': side,
        'type': 'MARKET',
        'quantity': round(quantity, 6),
        'timestamp': ts
    }
    signed = sign(params)
    r = requests.post(
        f"{BASE_URL}/api/v3/order",
        headers={'X-MBX-APIKEY': API_KEY, 'Content-Type': 'application/x-www-form-urlencoded'},
        data=signed
    )
    data = r.json()
    if 'code' in data:
        raise Exception(f"Error orden: {data['msg']}")
    return data

def swap(from_coin: str, to_coin: str, balances: dict, prices: dict):
    # Vender from_coin a USDT
    sell_qty = round(balances[from_coin] * 0.999, 6)
    log(f"  Vendiendo {sell_qty} {from_coin} → USDT")
    sell_result = execute_order(from_coin + 'USDT', 'SELL', sell_qty)
    usdt_received = float(sell_result['cummulativeQuoteQty']) * 0.999
    log(f"  Recibido: {usdt_received:.2f} USDT")

    time.sleep(1.5)

    # Comprar to_coin con USDT
    buy_qty = round(usdt_received / prices[to_coin] * 0.999, 6)
    log(f"  Comprando {buy_qty} {to_coin} con USDT")
    buy_result = execute_order(to_coin + 'USDT', 'BUY', buy_qty)
    log(f"  Compra ejecutada: {buy_result.get('executedQty', '?')} {to_coin}")

def bot_cycle():
    global current_coin, base_prices, total_gain

    try:
        prices = get_prices()

        # Inicializar precios base
        for c in COINS:
            if c not in base_prices:
                base_prices[c] = prices[c]
                log(f"Precio base {c}: {prices[c]}")

        # Calcular % desde base
        pcts = {c: (prices[c] - base_prices[c]) / base_prices[c] * 100 for c in COINS}
        best_coin = min(pcts, key=pcts.get)
        best_pct  = pcts[best_coin]
        cur_pct   = pcts[current_coin]
        diff      = abs(best_pct - cur_pct)

        log(f"En: {current_coin} ({cur_pct:.2f}%) | Mejor: {best_coin} ({best_pct:.2f}%) | Diff: {diff:.2f}%")

        if best_coin != current_coin and diff >= THRESHOLD:
            log(f"★ Rotando {current_coin} → {best_coin} (diff {diff:.2f}%)")
            balances = get_balances()
            swap(current_coin, best_coin, balances, prices)

            net = diff - COMMISSION
            total_gain += net
            op = {
                'time': datetime.now().strftime('%H:%M:%S'),
                'from': current_coin,
                'to': best_coin,
                'diff': round(diff, 2),
                'net': round(net, 2),
                'total': round(total_gain, 2)
            }
            ops.append(op)
            current_coin = best_coin
            log(f"✓ Rotación completada | Ganancia neta: +{net:.2f}% | Acumulado: +{total_gain:.2f}%")
        else:
            log(f"Sin rotación (diff {diff:.2f}% < umbral {THRESHOLD}%)")

    except Exception as e:
        log(f"ERROR: {e}")

def main():
    log("=" * 50)
    log(f"BOT CRYPTO ROTACIÓN iniciado")
    log(f"Umbral: {THRESHOLD}% | Moneda inicial: {current_coin}")
    log(f"Monedas: {', '.join(COINS)}")
    log(f"Comisión por operación: {COMMISSION}%")
    log("=" * 50)

    if not API_KEY or not SECRET_KEY:
        log("ERROR: Faltan BINANCE_API_KEY o BINANCE_SECRET_KEY")
        log("Seteá las variables de entorno antes de correr el bot")
        return

    while True:
        bot_cycle()
        log(f"Esperando {INTERVAL}s...")
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
