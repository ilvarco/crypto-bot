#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_reversion.py
-----------------
Compara reglas de REVERSION A LA MEDIA contra el COMBO trend (SAR+Supertrend)
en NEAR/BTC 4H. La pregunta: hay una regla que capture seguido y chico
(sin depender del pump) y que SOBREVIVA a las fees en 4H.

Logica de reversion (en clave acumulacion BTC):
  - Entras a NEAR cuando esta SOBREVENDIDO vs BTC (NEAR barato).
  - Volves a BTC cuando REVIRTIO a la media (NEAR se recupero).
  El rebote es la captura. El pump, si viene, es bonus.

Todo en BTC, con fees, velas cerradas. Correr en la VPS: python3 test_reversion.py
"""

import urllib.request, json, time
import numpy as np
import pandas as pd

# ------------------ parametros ------------------
SYMBOL   = "NEARBTC"
INTERVAL = "4h"
DIAS     = 250
FEE      = 0.001        # 0.1% por lado
ST_PER, ST_MULT = 10, 3.0
SAR_AF0, SAR_STEP, SAR_MAX = 0.02, 0.02, 0.20
# ------------------------------------------------


def get_klines(symbol, interval, dias):
    ms = {"4h": 4*3600*1000}[interval]
    n_obj = int(dias*24*3600*1000/ms) + 60
    end = int(time.time()*1000)
    rows = []
    while len(rows) < n_obj:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        batch = json.load(urllib.request.urlopen(req, timeout=20))
        if not batch:
            break
        rows = batch + rows
        end = batch[0][0] - 1
        if len(batch) < 1000:
            break
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=["ot","o","h","l","c","v","ct","qv","n","tb","tq","ig"])
    for col in ["o","h","l","c"]:
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["ot"], unit="ms")
    df = df[["time","o","h","l","c"]].drop_duplicates("time").reset_index(drop=True)
    return df.iloc[-(n_obj):].reset_index(drop=True)


# ------------------ indicadores ------------------
def rma(series, n):
    arr = series.to_numpy(dtype=float)
    out = np.full(len(arr), np.nan)
    valid = np.where(~np.isnan(arr))[0]
    if len(valid) < n:
        return pd.Series(out, index=series.index)
    s = valid[0]
    out[s+n-1] = np.mean(arr[s:s+n])
    for i in range(s+n, len(arr)):
        out[i] = (out[i-1]*(n-1) + arr[i]) / n
    return pd.Series(out, index=series.index)


def wilder_atr(df, n):
    h, l, c = df["h"], df["l"], df["c"]
    pc = c.shift(1)
    tr = pd.concat([(h-l), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - l.iloc[0]
    return rma(tr, n)


def supertrend_dir(df, period, mult):
    atr = wilder_atr(df, period)
    hl2 = (df["h"] + df["l"]) / 2
    up = (hl2 + mult*atr).to_numpy()
    dn = (hl2 - mult*atr).to_numpy()
    c = df["c"].values
    n = len(df)
    fup = up.copy(); fdn = dn.copy()
    for i in range(1, n):
        if np.isnan(fup[i-1]):
            continue
        fup[i] = up[i] if (up[i] < fup[i-1] or c[i-1] > fup[i-1]) else fup[i-1]
        fdn[i] = dn[i] if (dn[i] > fdn[i-1] or c[i-1] < fdn[i-1]) else fdn[i-1]
    d = np.zeros(n, dtype=int); d[period] = 1
    for i in range(period+1, n):
        if d[i-1] == 1:
            d[i] = -1 if c[i] < fdn[i] else 1
        else:
            d[i] = 1 if c[i] > fup[i] else -1
    return pd.Series(d, index=df.index)


def sar_dir(df, af0, step, afmax):
    h = df["h"].values; l = df["l"].values
    n = len(df)
    sar = np.zeros(n); trend = np.zeros(n, dtype=int)
    trend[0] = 1; sar[0] = l[0]; ep = h[0]; af = af0
    for i in range(1, n):
        prev = sar[i-1]
        if trend[i-1] == 1:
            sar[i] = min(prev + af*(ep-prev), l[i-1], l[i-2] if i >= 2 else l[i-1])
            if h[i] > ep: ep = h[i]; af = min(af+step, afmax)
            if l[i] < sar[i]: trend[i] = -1; sar[i] = ep; ep = l[i]; af = af0
            else: trend[i] = 1
        else:
            sar[i] = max(prev + af*(ep-prev), h[i-1], h[i-2] if i >= 2 else h[i-1])
            if l[i] < ep: ep = l[i]; af = min(af+step, afmax)
            if h[i] > sar[i]: trend[i] = 1; sar[i] = ep; ep = h[i]; af = af0
            else: trend[i] = -1
    return pd.Series(trend, index=df.index)


def rsi(close, n):
    d = close.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = rma(gain, n); al = rma(loss, n)
    rs = ag / al.replace(0, np.nan)
    return 100 - 100/(1+rs)


def bollinger(close, n=20, k=2.0):
    ma = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return ma, ma + k*sd, ma - k*sd


# ------------------ generador de señal stateful ------------------
def reversion_signal(entry_cond, exit_cond):
    """Entra cuando entry_cond, MANTIENE hasta exit_cond. Devuelve hold_near bool."""
    e = entry_cond.fillna(False).values
    x = exit_cond.fillna(False).values
    hold = np.zeros(len(e), dtype=bool)
    in_pos = False
    for i in range(len(e)):
        if not in_pos and e[i]:
            in_pos = True
        elif in_pos and x[i]:
            in_pos = False
        hold[i] = in_pos
    return pd.Series(hold, index=entry_cond.index)


# ------------------ motor de backtest ------------------
def backtest(df, hold_near, fee, start):
    c = df["c"].values
    sig = hold_near.values
    btc, near = 1.0, 0.0
    pos = "BTC"; entry = None
    eq = []; tramos = []; cambios = 0; en_near = 0
    for i in range(start, len(df)):
        want = "NEAR" if sig[i] else "BTC"
        if want != pos:
            cambios += 1
            if want == "NEAR":
                near = (btc*(1-fee))/c[i]; btc = 0.0; entry = c[i]; pos = "NEAR"
            else:
                btc = near*c[i]*(1-fee); tramos.append(c[i]/entry-1)
                near = 0.0; pos = "BTC"
        if pos == "NEAR":
            en_near += 1
        eq.append(btc + near*c[i])
    eq = pd.Series(eq)
    dd = (eq/eq.cummax() - 1).min()
    n_bt = len(df) - start
    return {
        "ret_pct": (eq.iloc[-1]-1)*100,
        "max_dd_pct": dd*100,
        "round_trips": len(tramos),
        "win_rate": (np.mean([t > 0 for t in tramos])*100) if tramos else 0.0,
        "avg_cap": (np.mean(tramos)*100) if tramos else 0.0,
        "pct_en_near": en_near/n_bt*100,
    }


# ------------------ run ------------------
def main():
    print(f"Bajando {DIAS} dias de {SYMBOL} {INTERVAL}...")
    df = get_klines(SYMBOL, INTERVAL, DIAS)
    df = df.iloc[:-1].reset_index(drop=True)
    print(f"  {len(df)} velas cerradas | {df['time'].iloc[0]} -> {df['time'].iloc[-1]}\n")

    c = df["c"]
    combo = (supertrend_dir(df, ST_PER, ST_MULT) == 1) & (sar_dir(df, SAR_AF0, SAR_STEP, SAR_MAX) == 1)
    r14 = rsi(c, 14)
    r2  = rsi(c, 2)
    ma20, bb_up, bb_lo = bollinger(c, 20, 2)

    variantes = {
        "COMBO trend (base)":      combo,
        "RSI14  <30 in / >50 out": reversion_signal(r14 < 30, r14 > 50),
        "RSI14  <30 in / >55 out": reversion_signal(r14 < 30, r14 > 55),
        "RSI14  <35 in / >55 out": reversion_signal(r14 < 35, r14 > 55),
        "Bollinger  baja->media":  reversion_signal(c < bb_lo, c >= ma20),
        "RSI2   <10 in / >70 out": reversion_signal(r2 < 10, r2 > 70),
    }

    start = 40
    res = {nom: backtest(df, sig, FEE, start) for nom, sig in variantes.items()}

    print(f"{'estrategia':<26}{'retBTC':>8}{'maxDD':>8}{'trades':>7}{'win%':>6}{'capt.med':>9}{'%enNEAR':>9}")
    print("-"*73)
    for nom, r in res.items():
        print(f"{nom:<26}{r['ret_pct']:>7.2f}%{r['max_dd_pct']:>7.2f}%"
              f"{r['round_trips']:>7}{r['win_rate']:>5.0f}%{r['avg_cap']:>8.2f}%{r['pct_en_near']:>8.1f}%")
    print("-"*73)
    print("retBTC = cuanto BTC sumaste | capt.med = captura promedio por trade")
    print("Lo que buscas: win% alto + muchas capturas chicas positivas SIN depender de % en NEAR alto.")
    print("Lo que delata fee-drag: muchos trades pero retBTC chato o negativo, captura media < ~0.3%.")
    print("Lo que delata falling-knife (cuchillo cayendo): maxDD feo = compraste barato y siguio bajando.")


if __name__ == "__main__":
    main()
