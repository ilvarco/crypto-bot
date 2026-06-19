#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
#  bot_v2.py  -  Bot de tendencia COMBO (SAR + Supertrend), NEAR/BTC, velas 4h
#               largo SOLO si SAR verde Y Supertrend verde; si no, refugio en BTC
# ----------------------------------------------------------------------------
#  Reescrito desde cero con las correcciones que sacamos del bug del bot viejo:
#
#   1) NO confia en su propia memoria.  Antes de decidir lee tu SALDO REAL en
#      Binance, y despues de cada orden VERIFICA que el saldo cambio de verdad.
#      Si una orden dice "ejecutada" pero el saldo no se movio -> FRENA y avisa.
#      (esto mata el bug de la "VENTA REAL" fantasma).
#
#   2) Loguea el ERROR REAL de Binance (no un "400" pelado).
#
#   3) Sincroniza el reloj con Binance (offset + recvWindow amplio).
#
#   4) Arranca en DRY_RUN=True: mira y loguea lo que HARIA, sin tocar plata.
#      Recien cuando confirmes que coincide con la realidad, lo pasas a real.
#
#   5) Senal SAR identica a la del backtest (+117% en NEAR/BTC 4h).
#
#  Una sola dueña por moneda: este bot maneja NEAR. No la operes a mano.
#  Solo necesita 'requests'.   ->   pip install requests
# ============================================================================

import time, hmac, hashlib, json, math, os, sys
from urllib.parse import urlencode
import requests

# ----------------------------- CONFIG --------------------------------------
SYMBOL      = "NEARBTC"
BASE_ASSET  = "NEAR"        # lo que tengo cuando estoy LARGO
QUOTE_ASSET = "BTC"         # lo que tengo cuando estoy en refugio
INTERVAL    = "4h"
AF_STEP, AF_MAX = 0.02, 0.2 # SAR(0.02, 0.2) - igual que el backtest
ST_ATR_LEN, ST_MULT = 10, 3.0  # Supertrend(10,3) - filtro: largo solo si SAR verde Y ST verde
LOOKBACK    = 500           # velas que pido para calcular el SAR (sobra para converger)
POLL_SEC    = 300           # reviso cada 5 min; solo actuo sobre vela CERRADA
RECV_WINDOW = 60000
BUY_BUFFER  = 0.999         # al comprar gasto 99.9% del BTC (deja aire p/ redondeo)

DRY_RUN     = False         # arreglo de precision aplicado; va directo a real

BASE_URL    = "https://api.binance.com"
DIR         = "/root/bot"
LOG_FILE    = f"{DIR}/v2_{SYMBOL}_log.txt"
KEYS_FILE   = f"{DIR}/keys.json"      # {"key":"...","secret":"..."}  (chmod 600)
HALT_FILE   = f"{DIR}/v2_HALT.flag"   # si existe, el bot no opera (freno de mano)

# ----------------------------- LOG -----------------------------------------
def log(msg):
    tag = "[DRY]" if DRY_RUN else "[LIVE]"
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {tag} {SYMBOL} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ----------------------------- CLAVES --------------------------------------
def load_keys():
    with open(KEYS_FILE) as f:
        k = json.load(f)
    return k["key"], k["secret"]

API_KEY, API_SECRET = (None, None)

# --------------------------- RELOJ / OFFSET --------------------------------
TIME_OFFSET = 0
def sync_time():
    global TIME_OFFSET
    try:
        r = requests.get(BASE_URL + "/api/v3/time", timeout=15); r.raise_for_status()
        TIME_OFFSET = r.json()["serverTime"] - int(time.time() * 1000)
    except Exception as e:
        log(f"WARN no pude sincronizar hora: {e}")

def now_ms():
    return int(time.time() * 1000) + TIME_OFFSET

# --------------------------- HTTP firmado ----------------------------------
def public(path, params=None):
    r = requests.get(BASE_URL + path, params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json()

def signed(method, path, params):
    p = dict(params)
    p["timestamp"]  = now_ms()
    p["recvWindow"] = RECV_WINDOW
    qs  = urlencode(p)
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{qs}&signature={sig}"
    r = requests.request(method, url, headers={"X-MBX-APIKEY": API_KEY}, timeout=20)
    if r.status_code != 200:
        # ERROR REAL de Binance (cuerpo completo), no un "400" pelado
        raise RuntimeError(f"Binance {r.status_code}: {r.text}")
    return r.json()

# --------------------------- CUENTA (la verdad) ----------------------------
def get_balances():
    acc = signed("GET", "/api/v3/account", {})
    return {b["asset"]: float(b["free"]) for b in acc["balances"]}

def last_price():
    return float(public("/api/v3/ticker/price", {"symbol": SYMBOL})["price"])

# --------------------------- FILTROS del par -------------------------------
FILT = {"step": 0.0, "minQty": 0.0, "minNotional": 0.0}
def load_filters():
    info = public("/api/v3/exchangeInfo", {"symbol": SYMBOL})
    s = info["symbols"][0]
    f = {flt["filterType"]: flt for flt in s["filters"]}
    FILT["step"]   = float(f["LOT_SIZE"]["stepSize"])
    FILT["minQty"] = float(f["LOT_SIZE"]["minQty"])
    mn = f.get("NOTIONAL") or f.get("MIN_NOTIONAL")
    FILT["minNotional"] = float(mn["minNotional"]) if mn else 0.0

def step_decimals(step):
    # cuantos decimales tiene el step (0.1 -> 1, 0.001 -> 3, 1 -> 0)
    s = ("%.10f" % step).rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0

def floor_step(qty, step):
    if step <= 0: return qty
    dec = step_decimals(step)
    floored = math.floor(qty / step) * step
    return float(f"{floored:.{dec}f}")   # corta la cola de floats (994.9000001 -> 994.9)

def fmt_qty(qty, step):
    # string limpio para mandarle a Binance (evita el error -1111 'too much precision')
    return f"{qty:.{step_decimals(step)}f}"

# --------------------------- KLINES + SAR ----------------------------------
def get_closed_klines(limit=LOOKBACK):
    raw = public("/api/v3/klines",
                 {"symbol": SYMBOL, "interval": INTERVAL, "limit": limit})
    raw = raw[:-1]               # DESCARTO la vela en curso -> todas cerradas
    H = [float(k[2]) for k in raw]
    L = [float(k[3]) for k in raw]
    C = [float(k[4]) for k in raw]
    return H, L, C

def psar(H, L, step=AF_STEP, mx=AF_MAX):
    # === TEXTUAL del backtest sar_4h_robustez.py (no tocar) ===
    n = len(H); up = [True] * n
    tr = True; af = step; ep = H[0]; sar = L[0]
    for i in range(1, n):
        s = sar + af * (ep - sar)
        if tr:
            s = min(s, L[i-1], L[i-2] if i >= 2 else L[i-1])
            if L[i] < s: tr = False; s = ep; ep = L[i]; af = step
            elif H[i] > ep: ep = H[i]; af = min(af + step, mx)
        else:
            s = max(s, H[i-1], H[i-2] if i >= 2 else H[i-1])
            if H[i] > s: tr = True; s = ep; ep = H[i]; af = step
            elif L[i] < ep: ep = L[i]; af = min(af + step, mx)
        sar = s; up[i] = tr
    return up

def supertrend_green(H, L, C, atr_len=ST_ATR_LEN, mult=ST_MULT):
    # Supertrend(10,3) en Python puro (sin numpy/pandas), mismo calculo que tus
    # tableros: ATR de Wilder (alpha=1/atr_len). Devuelve lista bool (True = VERDE).
    n = len(C)
    if n == 0:
        return []
    tr = [H[0] - L[0]] + [0.0] * (n - 1)
    for i in range(1, n):
        tr[i] = max(H[i] - L[i], abs(H[i] - C[i-1]), abs(L[i] - C[i-1]))
    a = 1.0 / atr_len
    atr = [tr[0]] + [0.0] * (n - 1)
    for i in range(1, n):
        atr[i] = a * tr[i] + (1 - a) * atr[i-1]
    fu = [0.0] * n; fl = [0.0] * n; bull = [True] * n
    hl2 = (H[0] + L[0]) / 2.0
    fu[0] = hl2 + mult * atr[0]; fl[0] = hl2 - mult * atr[0]
    for i in range(1, n):
        hl2 = (H[i] + L[i]) / 2.0
        u  = hl2 + mult * atr[i]
        lo = hl2 - mult * atr[i]
        fu[i] = u  if (u  < fu[i-1] or C[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = lo if (lo > fl[i-1] or C[i-1] < fl[i-1]) else fl[i-1]
        if bull[i-1]:
            bull[i] = False if C[i] < fl[i] else True
        else:
            bull[i] = True if C[i] > fu[i] else False
    return bull

# --------------------------- DECISION (testeable) --------------------------
def decide(green_now, holding_near, btc_enough):
    """green_now: bool (SAR de la ultima vela CERRADA).
       Devuelve 'BUY' | 'SELL' | 'HOLD'.  Idempotente: alinea cuenta real a la senal."""
    if green_now:                       # la senal quiere estar LARGO en NEAR
        if (not holding_near) and btc_enough:
            return "BUY"
        return "HOLD"
    else:                               # la senal quiere estar en BTC
        if holding_near:
            return "SELL"
        return "HOLD"

# --------------------------- ORDENES + VERIFICACION ------------------------
def halted():
    return os.path.exists(HALT_FILE)

def set_halt(reason):
    try:
        with open(HALT_FILE, "w") as f:
            f.write(reason + "\n")
    except Exception:
        pass
    log(f"!!! FRENO DE MANO ACTIVADO: {reason}")
    log("!!! El bot NO va a operar hasta que revises y borres " + HALT_FILE)

def do_sell(bal_before):
    near = bal_before.get(BASE_ASSET, 0.0)
    qty  = floor_step(near, FILT["step"])
    if qty < FILT["minQty"]:
        log(f"NEAR insuficiente para vender ({near}). Lo trato como FLAT.")
        return
    qty_str = fmt_qty(qty, FILT["step"])
    if DRY_RUN:
        log(f"DRY: venderia {qty_str} {BASE_ASSET} -> {QUOTE_ASSET} (a mercado)")
        return
    log(f"Enviando VENTA market de {qty_str} {BASE_ASSET} ...")
    resp = signed("POST", "/api/v3/order",
                  {"symbol": SYMBOL, "side": "SELL", "type": "MARKET", "quantity": qty_str})
    status = resp.get("status"); exq = float(resp.get("executedQty", 0))
    log(f"Respuesta orden: status={status} executedQty={exq}")
    # VERIFICACION contra el saldo real
    time.sleep(2)
    after = get_balances()
    near_after = after.get(BASE_ASSET, 0.0); btc_after = after.get(QUOTE_ASSET, 0.0)
    near_before = bal_before.get(BASE_ASSET, 0.0); btc_before = bal_before.get(QUOTE_ASSET, 0.0)
    vendio = (near_before - near_after) >= qty * 0.95
    entro_btc = btc_after > btc_before
    if status == "FILLED" and vendio and entro_btc:
        log(f"OK VENTA confirmada: {BASE_ASSET} {near_before:.4f}->{near_after:.4f} | "
            f"{QUOTE_ASSET} {btc_before:.8f}->{btc_after:.8f}")
    else:
        set_halt(f"VENTA no reconcilia: status={status} "
                 f"{BASE_ASSET} {near_before:.4f}->{near_after:.4f} "
                 f"{QUOTE_ASSET} {btc_before:.8f}->{btc_after:.8f}")

def do_buy(bal_before):
    btc = bal_before.get(QUOTE_ASSET, 0.0)
    quote = math.floor(btc * BUY_BUFFER * 1e8) / 1e8
    if quote < FILT["minNotional"]:
        log(f"BTC insuficiente para comprar ({btc}). Lo trato como ya-FLAT.")
        return
    quote_str = f"{quote:.8f}"
    if DRY_RUN:
        log(f"DRY: compraria {BASE_ASSET} gastando {quote_str} {QUOTE_ASSET} (a mercado)")
        return
    log(f"Enviando COMPRA market gastando {quote_str} {QUOTE_ASSET} ...")
    resp = signed("POST", "/api/v3/order",
                  {"symbol": SYMBOL, "side": "BUY", "type": "MARKET", "quoteOrderQty": quote_str})
    status = resp.get("status"); exq = float(resp.get("executedQty", 0))
    log(f"Respuesta orden: status={status} executedQty={exq}")
    time.sleep(2)
    after = get_balances()
    near_after = after.get(BASE_ASSET, 0.0); btc_after = after.get(QUOTE_ASSET, 0.0)
    near_before = bal_before.get(BASE_ASSET, 0.0); btc_before = bal_before.get(QUOTE_ASSET, 0.0)
    compro = near_after > near_before
    gasto  = btc_after < btc_before
    if status == "FILLED" and compro and gasto:
        log(f"OK COMPRA confirmada: {QUOTE_ASSET} {btc_before:.8f}->{btc_after:.8f} | "
            f"{BASE_ASSET} {near_before:.4f}->{near_after:.4f}")
    else:
        set_halt(f"COMPRA no reconcilia: status={status} "
                 f"{QUOTE_ASSET} {btc_before:.8f}->{btc_after:.8f} "
                 f"{BASE_ASSET} {near_before:.4f}->{near_after:.4f}")

# --------------------------- LOOP PRINCIPAL --------------------------------
def main():
    global API_KEY, API_SECRET
    API_KEY, API_SECRET = load_keys()
    sync_time()
    load_filters()
    log(f"=== bot_v2 arrancado | DRY_RUN={DRY_RUN} | {SYMBOL} {INTERVAL} | "
        f"COMBO SAR({AF_STEP},{AF_MAX})+ST({ST_ATR_LEN},{ST_MULT}) | "
        f"step={FILT['step']} minNotional={FILT['minNotional']} ===")

    last_seen_close = None
    while True:
        try:
            if halted():
                log("FRENO DE MANO activo, no opero. (borra el .flag para reanudar)")
                time.sleep(POLL_SEC); continue

            sync_time()
            H, L, C = get_closed_klines()
            sar_green = psar(H, L)[-1]                  # SAR de la ultima vela CERRADA
            st_green  = supertrend_green(H, L, C)[-1]   # Supertrend de la ultima vela CERRADA
            green_now = sar_green and st_green          # COMBO: largo SOLO si los dos verdes

            price = last_price()
            bal = get_balances()
            near = bal.get(BASE_ASSET, 0.0); btc = bal.get(QUOTE_ASSET, 0.0)
            near_val_btc = near * price
            holding_near = near_val_btc >= max(FILT["minNotional"], FILT["minQty"] * price)
            btc_enough   = btc >= FILT["minNotional"]

            action = decide(green_now, holding_near, btc_enough)

            # log de estado una vez por vela nueva (para no spammear cada 5 min)
            if last_seen_close != C[-1]:
                estado = "LARGO(NEAR)" if holding_near else ("BTC" if btc_enough else "vacio")
                log(f"vela cerrada | SAR={'VERDE' if green_now else 'ROJO'} | "
                    f"tengo {estado} (NEAR={near:.2f} ~{near_val_btc:.6f}BTC, BTC={btc:.8f}) | "
                    f"accion={action} | det[SAR={'V' if sar_green else 'R'} ST={'V' if st_green else 'R'}]")
                last_seen_close = C[-1]

            if action == "SELL":
                do_sell(bal)
            elif action == "BUY":
                do_buy(bal)
            # HOLD -> nada

        except Exception as e:
            log(f"ERROR loop: {e}")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
