#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
#  bot_v2.py  -  Bot de tendencia SAR, NEAR/BTC, velas 4h, largo-solo-en-verde
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
LOOKBACK    = 500           # velas que pido para calcular el SAR (sobra para converger)
POLL_SEC    = 300           # reviso cada 5 min; solo actuo sobre vela CERRADA
RECV_WINDOW = 60000
BUY_BUFFER  = 0.999         # al comprar gasto 99.9% del BTC (deja aire p/ redondeo)

DRY_RUN     = True          # <<<<<<  ARRANCA ASI. Pasar a False solo tras validar.

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

def floor_step(qty, step):
    if step <= 0: return qty
    return math.floor(qty / step) * step

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
