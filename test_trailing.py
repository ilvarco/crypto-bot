#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_trailing.py
----------------
Palanca A: refinar la SALIDA del combo (SAR+Supertrend) en NEAR/BTC 4H.
En vez de esperar el flip del Supertrend para salir, probamos un trailing
stop por ATR (chandelier) que corta los perdedores mas rapido y deja correr
los ganadores. Tambien testea tu stop fijo manual (2% y 3%).

Entrada: igual en todas (combo verde = SAR verde Y Supertrend verde).
Solo cambia la SALIDA. Asi aislamos el efecto de la salida.

Todo en BTC, con fees, velas cerradas. Correr en la VPS: python3 test_trailing.py
"""

import urllib.request, json, time
import numpy as np
import pandas as pd

SYMBOL   = "NEARBTC"
INTERVAL = "4h"
DIAS     = 250
FEE      = 0.001
ST_PER, ST_MULT = 10, 3.0
SAR_AF0, SAR_STEP, SAR_MAX = 0.02, 0.02, 0.20
ATR_TRAIL = 14            # ATR para el chandelier


def get_klines(symbol, interval, dias):
    ms = {"4h": 4*3600*1000}[interval]
    n_obj = int(dias*24*3600*1000/ms) + 60
    end = int(time.time()*1000); rows = []
    while len(rows) < n_obj:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        batch = json.load(urllib.request.urlopen(req, timeout=20))
        if not batch: break
        rows = batch + rows; end = batch[0][0]-1
        if len(batch) < 1000: break
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=["ot","o","h","l","c","v","ct","qv","n","tb","tq","ig"])
    for col in ["o","h","l","c"]: df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["ot"], unit="ms")
    df = df[["time","o","h","l","c"]].drop_duplicates("time").reset_index(drop=True)
    return df.iloc[-(n_obj):].reset_index(drop=True)


def rma(series, n):
    arr = series.to_numpy(dtype=float); out = np.full(len(arr), np.nan)
    valid = np.where(~np.isnan(arr))[0]
    if len(valid) < n: return pd.Series(out, index=series.index)
    s = valid[0]; out[s+n-1] = np.mean(arr[s:s+n])
    for i in range(s+n, len(arr)): out[i] = (out[i-1]*(n-1)+arr[i])/n
    return pd.Series(out, index=series.index)


def wilder_atr(df, n):
    h, l, c = df["h"], df["l"], df["c"]; pc = c.shift(1)
    tr = pd.concat([(h-l), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0]-l.iloc[0]
    return rma(tr, n)


def supertrend_dir(df, period, mult):
    atr = wilder_atr(df, period); hl2 = (df["h"]+df["l"])/2
    up = (hl2+mult*atr).to_numpy(); dn = (hl2-mult*atr).to_numpy()
    c = df["c"].values; n = len(df); fup = up.copy(); fdn = dn.copy()
    for i in range(1, n):
        if np.isnan(fup[i-1]): continue
        fup[i] = up[i] if (up[i] < fup[i-1] or c[i-1] > fup[i-1]) else fup[i-1]
        fdn[i] = dn[i] if (dn[i] > fdn[i-1] or c[i-1] < fdn[i-1]) else fdn[i-1]
    d = np.zeros(n, dtype=int); d[period] = 1
    for i in range(period+1, n):
        d[i] = (-1 if c[i] < fdn[i] else 1) if d[i-1] == 1 else (1 if c[i] > fup[i] else -1)
    return pd.Series(d, index=df.index)


def sar_dir(df, af0, step, afmax):
    h = df["h"].values; l = df["l"].values; n = len(df)
    sar = np.zeros(n); trend = np.zeros(n, dtype=int)
    trend[0] = 1; sar[0] = l[0]; ep = h[0]; af = af0
    for i in range(1, n):
        prev = sar[i-1]
        if trend[i-1] == 1:
            sar[i] = min(prev+af*(ep-prev), l[i-1], l[i-2] if i >= 2 else l[i-1])
            if h[i] > ep: ep = h[i]; af = min(af+step, afmax)
            if l[i] < sar[i]: trend[i] = -1; sar[i] = ep; ep = l[i]; af = af0
            else: trend[i] = 1
        else:
            sar[i] = max(prev+af*(ep-prev), h[i-1], h[i-2] if i >= 2 else h[i-1])
            if l[i] < ep: ep = l[i]; af = min(af+step, afmax)
            if h[i] > sar[i]: trend[i] = 1; sar[i] = ep; ep = h[i]; af = af0
            else: trend[i] = -1
    return pd.Series(trend, index=df.index)


# ------------------ motor con salida configurable ------------------
def backtest(df, combo, atr, exit_mode, X=None, hardstop=None, fee=FEE, start=40):
    """exit_mode: 'combo'      -> sale cuando el combo se pone rojo
                  'chandelier' -> sale por combo rojo O close < (max_high_desde_entrada - X*ATR)
                  'hardstop'   -> sale por combo rojo O close < entrada*(1-hardstop)"""
    c = df["c"].values; h = df["h"].values
    cb = combo.fillna(False).values; a = atr.values
    btc, near = 1.0, 0.0; pos = "BTC"; entry = None; hh = None
    eq = []; tramos = []; cambios = 0; en_near = 0
    for i in range(start, len(df)):
        if pos == "NEAR":
            hh = max(hh, h[i])
            salir = False
            if not cb[i]:
                salir = True
            elif exit_mode == "chandelier" and c[i] < hh - X*a[i]:
                salir = True
            elif exit_mode == "hardstop" and c[i] < entry*(1-hardstop):
                salir = True
            if salir:
                btc = near*c[i]*(1-fee); tramos.append(c[i]/entry-1)
                near = 0.0; pos = "BTC"; cambios += 1
        if pos == "BTC" and cb[i] and not cb[i-1]:        # entrada en flanco de subida
            near = (btc*(1-fee))/c[i]; btc = 0.0; entry = c[i]; hh = h[i]; pos = "NEAR"; cambios += 1
        if pos == "NEAR": en_near += 1
        eq.append(btc + near*c[i])
    eq = pd.Series(eq); dd = (eq/eq.cummax()-1).min(); n_bt = len(df)-start
    wins = [t for t in tramos if t > 0]; losses = [t for t in tramos if t <= 0]
    return {
        "ret_pct": (eq.iloc[-1]-1)*100, "max_dd_pct": dd*100,
        "round_trips": len(tramos),
        "win_rate": (len(wins)/len(tramos)*100) if tramos else 0.0,
        "avg_win": (np.mean(wins)*100) if wins else 0.0,
        "avg_loss": (np.mean(losses)*100) if losses else 0.0,
        "pct_en_near": en_near/n_bt*100,
    }


def main():
    print(f"Bajando {DIAS} dias de {SYMBOL} {INTERVAL}...")
    df = get_klines(SYMBOL, INTERVAL, DIAS)
    df = df.iloc[:-1].reset_index(drop=True)
    print(f"  {len(df)} velas cerradas | {df['time'].iloc[0]} -> {df['time'].iloc[-1]}\n")

    combo = (supertrend_dir(df, ST_PER, ST_MULT) == 1) & (sar_dir(df, SAR_AF0, SAR_STEP, SAR_MAX) == 1)
    atr = wilder_atr(df, ATR_TRAIL)

    variantes = [
        ("COMBO base (salida flip)",  dict(exit_mode="combo")),
        ("+ chandelier ATRx2.0",      dict(exit_mode="chandelier", X=2.0)),
        ("+ chandelier ATRx3.0",      dict(exit_mode="chandelier", X=3.0)),
        ("+ chandelier ATRx4.0",      dict(exit_mode="chandelier", X=4.0)),
        ("+ stop fijo 2%",            dict(exit_mode="hardstop", hardstop=0.02)),
        ("+ stop fijo 3%",            dict(exit_mode="hardstop", hardstop=0.03)),
    ]

    print(f"{'estrategia':<26}{'retBTC':>8}{'maxDD':>8}{'trades':>7}{'win%':>6}{'gan.med':>8}{'perd.med':>9}{'%enNEAR':>9}")
    print("-"*81)
    for nom, kw in variantes:
        r = backtest(df, combo, atr, **kw)
        print(f"{nom:<26}{r['ret_pct']:>7.2f}%{r['max_dd_pct']:>7.2f}%"
              f"{r['round_trips']:>7}{r['win_rate']:>5.0f}%{r['avg_win']:>7.2f}%"
              f"{r['avg_loss']:>8.2f}%{r['pct_en_near']:>8.1f}%")
    print("-"*81)
    print("gan.med = ganancia promedio de los trades ganadores | perd.med = perdida promedio de los perdedores")
    print("Objetivo: que 'perd.med' se achique (perdedores mas chicos) sin matar 'retBTC'.")
    print("Si un trailing baja maxDD Y mantiene o sube retBTC -> esa salida es mejor que esperar el flip.")


if __name__ == "__main__":
    main()
