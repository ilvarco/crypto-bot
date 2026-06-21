#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_adx_filtro.py
------------------
Compara el combo SAR + Supertrend(10,3) en NEAR/BTC 4H contra el MISMO combo
pero filtrado por ADX (solo mantener NEAR si la tendencia tiene fuerza).

Todo medido EN BTC, con fees, sobre velas CERRADAS. Nada de buy-and-hold:
la pregunta es si el filtro ADX hace crecer mas el BTC y/o recorta el drawdown.

Correr en la VPS:  python3 test_adx_filtro.py
"""

import urllib.request, json, time
import numpy as np
import pandas as pd

# ------------------ parametros ------------------
SYMBOL   = "NEARBTC"
INTERVAL = "4h"
DIAS     = 250          # ventana de backtest
FEE      = 0.001        # 0.1% por lado (0.2% round-trip)
ST_PER, ST_MULT = 10, 3.0
SAR_AF0, SAR_STEP, SAR_MAX = 0.02, 0.02, 0.20
ADX_N    = 14
UMBRALES_ADX = [15, 20, 25]   # filtros a probar
# ------------------------------------------------


def get_klines(symbol, interval, dias):
    ms = {"4h": 4*3600*1000, "6h": 6*3600*1000, "8h": 8*3600*1000,
          "12h": 12*3600*1000, "1d": 24*3600*1000}[interval]
    n_obj = int(dias*24*3600*1000/ms) + 60      # +60 de margen para warmup
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
    """Suavizado de Wilder (RMA), seed = SMA de los primeros n validos. Igual a TradingView/Binance."""
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
    """Devuelve 1 (verde) / -1 (rojo). Misma logica que el bot."""
    atr = wilder_atr(df, period)
    hl2 = (df["h"] + df["l"]) / 2
    up = hl2 + mult*atr
    dn = hl2 - mult*atr
    c = df["c"].values
    n = len(df)
    up = up.to_numpy(); dn = dn.to_numpy()
    fup = up.copy(); fdn = dn.copy()
    for i in range(1, n):
        if np.isnan(fup[i-1]):
            continue
        fup[i] = up[i] if (up[i] < fup[i-1] or c[i-1] > fup[i-1]) else fup[i-1]
        fdn[i] = dn[i] if (dn[i] > fdn[i-1] or c[i-1] < fdn[i-1]) else fdn[i-1]
    d = np.zeros(n, dtype=int)
    start = period
    d[start] = 1
    for i in range(start+1, n):
        if d[i-1] == 1:
            d[i] = -1 if c[i] < fdn[i] else 1
        else:
            d[i] = 1 if c[i] > fup[i] else -1
    return pd.Series(d, index=df.index)


def sar_dir(df, af0, step, afmax):
    """Parabolic SAR. Devuelve 1 (SAR abajo del precio = verde) / -1 (rojo)."""
    h = df["h"].values; l = df["l"].values
    n = len(df)
    sar = np.zeros(n); trend = np.zeros(n, dtype=int)
    trend[0] = 1; sar[0] = l[0]; ep = h[0]; af = af0
    for i in range(1, n):
        prev = sar[i-1]
        if trend[i-1] == 1:
            sar[i] = prev + af*(ep - prev)
            sar[i] = min(sar[i], l[i-1], l[i-2] if i >= 2 else l[i-1])
            if h[i] > ep:
                ep = h[i]; af = min(af+step, afmax)
            if l[i] < sar[i]:
                trend[i] = -1; sar[i] = ep; ep = l[i]; af = af0
            else:
                trend[i] = 1
        else:
            sar[i] = prev + af*(ep - prev)
            sar[i] = max(sar[i], h[i-1], h[i-2] if i >= 2 else h[i-1])
            if l[i] < ep:
                ep = l[i]; af = min(af+step, afmax)
            if h[i] > sar[i]:
                trend[i] = 1; sar[i] = ep; ep = h[i]; af = af0
            else:
                trend[i] = -1
    return pd.Series(trend, index=df.index)


def adx_dmi(df, n):
    """ADX, +DI, -DI estilo Binance (RMA de Wilder en todo)."""
    h, l, c = df["h"], df["l"], df["c"]
    up = h.diff()
    dn = -l.diff()
    plus_dm  = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pc = c.shift(1)
    tr = pd.concat([(h-l), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    atr   = rma(tr, n)
    p_di  = 100 * rma(plus_dm, n) / atr
    m_di  = 100 * rma(minus_dm, n) / atr
    dx    = 100 * (p_di - m_di).abs() / (p_di + m_di)
    adx   = rma(dx, n)
    return adx, p_di, m_di


# ------------------ motor de backtest ------------------
def backtest(df, hold_near, fee, start):
    """hold_near: serie bool (True = mantener NEAR, False = refugio BTC).
    Equity en BTC arrancando en 1.0. Devuelve metricas."""
    c = df["c"].values
    sig = hold_near.values
    btc, near = 1.0, 0.0
    pos = "BTC"; entry = None
    eq = []
    tramos = []           # retorno bruto de cada episodio en NEAR
    cambios = 0
    en_near = 0
    for i in range(start, len(df)):
        want = "NEAR" if sig[i] else "BTC"
        if want != pos:
            cambios += 1
            if want == "NEAR":
                near = (btc * (1-fee)) / c[i]; btc = 0.0; entry = c[i]; pos = "NEAR"
            else:
                btc = near * c[i] * (1-fee); tramos.append(c[i]/entry - 1)
                near = 0.0; pos = "BTC"
        if pos == "NEAR":
            en_near += 1
        eq.append(btc + near*c[i])
    eq = pd.Series(eq)
    # cerrar posicion abierta para la foto final (sin contar como trade ganado/perdido)
    final = eq.iloc[-1]
    pico = eq.cummax()
    dd = (eq/pico - 1).min()
    n_bt = len(df) - start
    return {
        "final_btc": final,
        "ret_pct": (final - 1) * 100,
        "max_dd_pct": dd * 100,
        "round_trips": len(tramos),
        "cambios": cambios,
        "win_rate": (np.mean([t > 0 for t in tramos]) * 100) if tramos else 0.0,
        "pct_en_near": en_near / n_bt * 100,
    }


# ------------------ run ------------------
def main():
    print(f"Bajando {DIAS} dias de {SYMBOL} {INTERVAL}...")
    df = get_klines(SYMBOL, INTERVAL, DIAS)
    df = df.iloc[:-1].reset_index(drop=True)     # descarto la vela en formacion
    print(f"  {len(df)} velas cerradas | {df['time'].iloc[0]} -> {df['time'].iloc[-1]}\n")

    st  = supertrend_dir(df, ST_PER, ST_MULT)
    sar = sar_dir(df, SAR_AF0, SAR_STEP, SAR_MAX)
    adx, p_di, m_di = adx_dmi(df, ADX_N)

    combo = (st == 1) & (sar == 1)               # base: mantener NEAR si ambos verdes
    start = 40                                    # arranco despues del warmup de todos

    variantes = {"COMBO (base, sin ADX)": combo}
    for u in UMBRALES_ADX:
        variantes[f"COMBO + ADX>{u}"] = combo & (adx > u)

    res = {nom: backtest(df, sig.fillna(False), FEE, start) for nom, sig in variantes.items()}

    # tabla
    print(f"{'estrategia':<26}{'ret BTC':>9}{'maxDD':>9}{'trades':>8}{'win%':>7}{'%enNEAR':>9}")
    print("-"*68)
    for nom, r in res.items():
        print(f"{nom:<26}{r['ret_pct']:>8.2f}%{r['max_dd_pct']:>8.2f}%"
              f"{r['round_trips']:>8}{r['win_rate']:>6.0f}%{r['pct_en_near']:>8.1f}%")
    print("-"*68)
    print(f"ADX actual (ultima cerrada): {adx.iloc[-1]:.1f} | +DI {p_di.iloc[-1]:.1f} | -DI {m_di.iloc[-1]:.1f}")
    print("\nLectura: si una fila con ADX mejora 'ret BTC' Y achica 'maxDD' a la vez,")
    print("el filtro suma. Si solo recorta trades sin mejorar el DD, no vale la pena.")


if __name__ == "__main__":
    main()
