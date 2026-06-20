#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
#  estado_server.py - sirve el estado REAL de los bots (leido de los logs) como
#  JSON, y tambien sirve la escalera. Puerto 8001. Solo lectura, no toca plata.
# ============================================================================
import re, json, os, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIR  = "/root/bot"
PORT = 8001
BOTS = [
    ("NEARBTC",   "NEAR",   "bot_v2",        "v2_HALT.flag"),
    ("RENDERBTC", "RENDER", "bot_v2_render", "v2_RENDERBTC_HALT.flag"),
]
PAGE = f"{DIR}/escalera_combinada.html"

RE_BUY  = re.compile(r"OK COMPRA confirmada: BTC ([\d.]+)->([\d.]+) \| (\w+) ([\d.]+)->([\d.]+)")
RE_SELL = re.compile(r"OK VENTA confirmada: (\w+) ([\d.]+)->([\d.]+) \| BTC ([\d.]+)->([\d.]+)")
RE_VELA = re.compile(r"vela cerrada \| SAR=(\w+) \| tengo (\S+) \(.*BTC=([\d.]+)\) \| accion=(\w+) \| det\[SAR=(\w) ST=(\w)\]")
RE_TS   = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

def parse_log(path):
    try:
        with open(path) as f:
            lines = f.readlines()[-400:]
    except Exception:
        return {"ok": False, "err": "sin log"}
    last_buy = last_sell = None; last_buy_i = last_sell_i = -1
    vela = None
    for i, ln in enumerate(lines):
        mb = RE_BUY.search(ln)
        if mb: last_buy = mb; last_buy_i = i
        ms = RE_SELL.search(ln)
        if ms: last_sell = ms; last_sell_i = i
        mv = RE_VELA.search(ln)
        if mv: vela = mv
    st = {"ok": True}
    if vela:
        st["sar"]    = vela.group(5)        # V / R
        st["st"]     = vela.group(6)
        st["accion"] = vela.group(4)
        st["btc"]    = float(vela.group(3))
    # posicion real = ultima orden confirmada (compra->LARGO, venta->BTC)
    if last_buy_i > last_sell_i and last_buy:
        a,b,coin,c,d = last_buy.groups()
        a,b,c,d = float(a),float(b),float(c),float(d)
        spent = a-b; got = d-c
        st["pos"]   = "LARGO"
        st["qty"]   = d
        st["entry"] = (spent/got) if got else 0.0
        st["spent"] = spent
        st["btc"]   = b
    else:
        st["pos"] = "BTC"
        if last_sell:
            st["btc"] = float(last_sell.group(5))
    return st

def svc_active(svc):
    try:
        r = subprocess.run(["systemctl","is-active",svc],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"

def estado():
    out = {}
    for sym, name, svc, haltf in BOTS:
        st = parse_log(f"{DIR}/v2_{sym}_log.txt")
        st["svc"]    = svc_active(svc)
        st["alive"]  = (st["svc"] == "active")
        st["halted"] = os.path.exists(f"{DIR}/{haltf}")
        out[sym] = st
    return out

def parse_trades(path, coin):
    # extrae todas las compras y ventas confirmadas del log, en orden
    try:
        with open(path) as f:
            lines = f.readlines()
    except Exception:
        return []
    evs = []
    for ln in lines:
        m = RE_TS.match(ln); ts = m.group(1) if m else ""
        mb = RE_BUY.search(ln)
        if mb:
            a,b,_,c,d = mb.groups()
            a,b,c,d = float(a),float(b),float(c),float(d)
            spent=a-b; qty=d-c
            if spent>0 and qty>0:
                evs.append({"ts":ts,"side":"buy","price":spent/qty,"btc":spent,"qty":qty})
            continue
        ms = RE_SELL.search(ln)
        if ms:
            _,c,d,a,b = ms.groups()
            c,d,a,b = float(c),float(d),float(a),float(b)
            got=b-a; qty=c-d
            if got>0 and qty>0:
                evs.append({"ts":ts,"side":"sell","price":got/qty,"btc":got,"qty":qty})
    return evs

def round_trips(evs, coin):
    # empareja compra->venta en operaciones cerradas; deja la compra suelta como abierta
    trips=[]; ob=None
    for e in evs:
        if e["side"]=="buy":
            ob=e
        elif e["side"]=="sell" and ob:
            trips.append({
                "coin":coin,"open_ts":ob["ts"],"close_ts":e["ts"],
                "entry":ob["price"],"exit":e["price"],
                "btc_in":ob["btc"],"btc_out":e["btc"],
                "res_btc":e["btc"]-ob["btc"],
                "res_pct":((e["price"]/ob["price"]-1)*100 if ob["price"] else 0.0),
            })
            ob=None
    op=None
    if ob:
        op={"coin":coin,"open_ts":ob["ts"],"entry":ob["price"],"btc_in":ob["btc"],"qty":ob["qty"]}
    return trips, op

def trades_payload():
    trips=[]; opens=[]
    for sym, name, svc, haltf in BOTS:
        t,o = round_trips(parse_trades(f"{DIR}/v2_{sym}_log.txt", name), name)
        trips += t
        if o: opens.append(o)
    trips.sort(key=lambda x:x["close_ts"])
    cum=0.0; accum=[]; wins=0
    for t in trips:
        cum += t["res_btc"]
        if t["res_btc"]>0: wins+=1
        accum.append({"ts":t["close_ts"],"cum":cum})
    return {"trips":trips,"open":opens,"accum":accum,
            "total_btc":cum,"n":len(trips),"wins":wins}

class H(BaseHTTPRequestHandler):
    def _h(self, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
    def do_GET(self):
        if self.path.startswith("/estado.json"):
            self._h("application/json")
            self.wfile.write(json.dumps(estado()).encode()); return
        if self.path.startswith("/trades.json"):
            self._h("application/json")
            self.wfile.write(json.dumps(trades_payload()).encode()); return
        if self.path in ("/", "/escalera", "/index.html"):
            try:
                with open(PAGE, "rb") as f: body = f.read()
                self._h("text/html"); self.wfile.write(body)
            except Exception:
                self._h("text/plain", 404); self.wfile.write(b"falta escalera_combinada.html")
            return
        self._h("text/plain", 404); self.wfile.write(b"no")
    def log_message(self, *a): pass

if __name__ == "__main__":
    print(f"estado_server en :{PORT}  (GET /estado.json  y  /)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
