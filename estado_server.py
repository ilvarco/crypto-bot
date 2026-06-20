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
