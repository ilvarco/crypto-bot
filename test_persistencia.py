#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_persistencia.py
--------------------
La pregunta que parte las aguas: el caracter de cada moneda (trend vs reversion)
SE MANTIENE de una ventana a otra, o se baraja (= ruido / espejismo)?

Baja ~2.5 anos, lo parte en ventanas de 6 meses, y en CADA ventana calcula
que estrategia le gana a cada moneda. Si NEAR es TREND en todas las ventanas
es un trender estructural. Si SOL es reversion en todas, es reversion estructural
y tu portafolio mixto tiene piso real. Si se mezclan, era espejismo de 8 meses.

Indicadores calculados sobre la historia COMPLETA (sin contaminar warmup), y
recien despues se mide cada ventana. Todo en BTC, con fees, velas cerradas.
Correr en la VPS: python3 test_persistencia.py   (tarda 1-2 min, baja mas historia)
"""

import urllib.request, json, time
import numpy as np
import pandas as pd

UNIVERSO = ["ETHBTC","SOLBTC","XRPBTC","ADABTC","AVAXBTC","LINKBTC","DOTBTC",
            "LTCBTC","ATOMBTC","INJBTC","NEARBTC","RENDERBTC","SUIBTC","FETBTC"]
INTERVAL = "4h"; DIAS = 900; FEE = 0.001
VENTANA_DIAS = 180                  # cada ventana ~6 meses
ST_PER, ST_MULT = 10, 3.0
SAR_AF0, SAR_STEP, SAR_MAX = 0.02, 0.02, 0.20


def get_klines(symbol, interval, dias):
    ms = {"4h": 4*3600*1000}[interval]; n_obj = int(dias*24*3600*1000/ms)+60
    end = int(time.time()*1000); rows = []
    try:
        while len(rows) < n_obj:
            url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
                   f"&interval={interval}&limit=1000&endTime={end}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            batch = json.load(urllib.request.urlopen(req, timeout=20))
            if not batch: break
            rows = batch + rows; end = batch[0][0]-1
            if len(batch) < 1000: break
            time.sleep(0.25)
    except Exception as e:
        print(f"   [skip] {symbol}: {e}"); return None
    if len(rows) < 300:
        print(f"   [skip] {symbol}: historia corta ({len(rows)} velas)"); return None
    df = pd.DataFrame(rows, columns=["ot","o","h","l","c","v","ct","qv","n","tb","tq","ig"])
    for col in ["o","h","l","c"]: df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["ot"], unit="ms")
    return df[["time","o","h","l","c"]].drop_duplicates("time").reset_index(drop=True).iloc[-(n_obj):].reset_index(drop=True)


def rma(series, n):
    arr = series.to_numpy(dtype=float); out = np.full(len(arr), np.nan)
    valid = np.where(~np.isnan(arr))[0]
    if len(valid) < n: return pd.Series(out, index=series.index)
    s = valid[0]; out[s+n-1] = np.mean(arr[s:s+n])
    for i in range(s+n, len(arr)): out[i] = (out[i-1]*(n-1)+arr[i])/n
    return pd.Series(out, index=series.index)


def wilder_atr(df, n):
    h,l,c = df["h"],df["l"],df["c"]; pc = c.shift(1)
    tr = pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); tr.iloc[0]=h.iloc[0]-l.iloc[0]
    return rma(tr, n)


def supertrend_dir(df, period, mult):
    atr = wilder_atr(df, period); hl2 = (df["h"]+df["l"])/2
    up = (hl2+mult*atr).to_numpy(); dn = (hl2-mult*atr).to_numpy()
    c = df["c"].values; n = len(df); fup = up.copy(); fdn = dn.copy()
    for i in range(1, n):
        if np.isnan(fup[i-1]): continue
        fup[i] = up[i] if (up[i] < fup[i-1] or c[i-1] > fup[i-1]) else fup[i-1]
        fdn[i] = dn[i] if (dn[i] > fdn[i-1] or c[i-1] < fdn[i-1]) else fdn[i-1]
    d = np.zeros(n, dtype=int); d[period]=1
    for i in range(period+1, n):
        d[i] = (-1 if c[i]<fdn[i] else 1) if d[i-1]==1 else (1 if c[i]>fup[i] else -1)
    return pd.Series(d, index=df.index)


def sar_dir(df, af0, step, afmax):
    h=df["h"].values; l=df["l"].values; n=len(df)
    sar=np.zeros(n); trend=np.zeros(n,dtype=int); trend[0]=1; sar[0]=l[0]; ep=h[0]; af=af0
    for i in range(1,n):
        prev=sar[i-1]
        if trend[i-1]==1:
            sar[i]=min(prev+af*(ep-prev), l[i-1], l[i-2] if i>=2 else l[i-1])
            if h[i]>ep: ep=h[i]; af=min(af+step,afmax)
            if l[i]<sar[i]: trend[i]=-1; sar[i]=ep; ep=l[i]; af=af0
            else: trend[i]=1
        else:
            sar[i]=max(prev+af*(ep-prev), h[i-1], h[i-2] if i>=2 else h[i-1])
            if l[i]<ep: ep=l[i]; af=min(af+step,afmax)
            if h[i]>sar[i]: trend[i]=1; sar[i]=ep; ep=h[i]; af=af0
            else: trend[i]=-1
    return pd.Series(trend, index=df.index)


def rsi(close, n):
    d=close.diff(); ag=rma(d.clip(lower=0),n); al=rma(-d.clip(upper=0),n)
    rs=ag/al.replace(0,np.nan); return 100-100/(1+rs)


def bollinger(close, n=20, k=2.0):
    ma=close.rolling(n).mean(); sd=close.rolling(n).std(ddof=0)
    return ma, ma+k*sd, ma-k*sd


def reversion_signal(entry_cond, exit_cond):
    e=entry_cond.fillna(False).values; x=exit_cond.fillna(False).values
    hold=np.zeros(len(e),dtype=bool); inp=False
    for i in range(len(e)):
        if not inp and e[i]: inp=True
        elif inp and x[i]: inp=False
        hold[i]=inp
    return pd.Series(hold, index=entry_cond.index)


def sleeve_bt(close, hold, fee):
    btc,coin=1.0,0.0; pos="BTC"; entry=None; eq=[]
    for i in range(len(close)):
        if pos=="COIN" and not hold[i]:
            btc=coin*close[i]*(1-fee); coin=0.0; pos="BTC"
        if pos=="BTC" and hold[i] and (i==0 or not hold[i-1]):
            coin=(btc*(1-fee))/close[i]; btc=0.0; entry=close[i]; pos="COIN"
        eq.append(btc+coin*close[i])
    return (pd.Series(eq).iloc[-1]-1)*100 if eq else 0.0


def main():
    print(f"Bajando {len(UNIVERSO)} pares {INTERVAL} (~{DIAS} dias)... (tarda un toque)")
    data = {}
    for sym in UNIVERSO:
        df = get_klines(sym, INTERVAL, DIAS)
        if df is None: continue
        df = df.iloc[:-1].reset_index(drop=True)
        c = df["c"]
        combo = (supertrend_dir(df,ST_PER,ST_MULT)==1) & (sar_dir(df,SAR_AF0,SAR_STEP,SAR_MAX)==1)
        ma,bbu,bbl = bollinger(c,20,2); r14 = rsi(c,14)
        data[sym] = pd.DataFrame({
            "close": c.values,
            "TREND": combo.fillna(False).values,
            "BOLL":  reversion_signal(c<bbl, c>=ma).values,
            "RSI":   reversion_signal(r14<30, r14>50).values,
        }, index=pd.DatetimeIndex(df["time"].values))
        time.sleep(0.15)

    if len(data) < 2:
        print("Pocas monedas."); return

    common = None
    for s in data.values():
        common = s.index if common is None else common.intersection(s.index)
    common = common.sort_values()

    win_bars = VENTANA_DIAS*6      # 6 velas de 4h por dia
    n_win = len(common)//win_bars
    if n_win < 2:
        print(f"Solo {len(common)} velas comunes ({len(common)/6:.0f} dias). No alcanza para 2 ventanas.")
        print("Recorta UNIVERSO sacando las monedas de historia corta y reintenta.")
        return
    common = common[-n_win*win_bars:]
    bordes = [common[i*win_bars] for i in range(n_win)] + [common[-1]]
    print(f"\n{len(data)} monedas | {n_win} ventanas de ~{VENTANA_DIAS} dias:")
    for i in range(n_win):
        print(f"  W{i+1}: {bordes[i].date()} -> {bordes[i+1].date()}")
    print()

    # mejor estrategia por moneda por ventana
    veredictos = {}
    print(f"{'moneda':<11}" + "".join(f"{'W'+str(i+1):>8}" for i in range(n_win)) + "   estable?")
    print("-"*(11+8*n_win+12))
    for sym in sorted(data):
        s = data[sym].loc[common]
        fila = []
        for i in range(n_win):
            sl = s.iloc[i*win_bars:(i+1)*win_bars]
            close = sl["close"].values
            rets = {k: sleeve_bt(close, sl[k].values, FEE) for k in ["TREND","BOLL","RSI"]}
            best = max(rets, key=rets.get)
            fila.append(best if rets[best] > 0 else "—")     # "—" si ninguna gana plata
        # veredicto: misma etiqueta (no "—") en mayoria de ventanas?
        etiquetas = [f for f in fila if f != "—"]
        if etiquetas:
            from collections import Counter
            top, cnt = Counter(etiquetas).most_common(1)[0]
            estable = f"{top} {cnt}/{n_win}" if cnt >= max(2, n_win-1) else "inestable"
        else:
            top, estable = "—", "nada gana"
        veredictos[sym] = (top, estable)
        print(f"{sym:<11}" + "".join(f"{v:>8}" for v in fila) + f"   {estable}")
    print("-"*(11+8*n_win+12))
    print('("—" = ninguna estrategia dio positivo esa ventana)\n')

    estables_trend = [s for s,(t,e) in veredictos.items() if t=="TREND" and "inestable" not in e and "nada" not in e]
    estables_rev   = [s for s,(t,e) in veredictos.items() if t in ("BOLL","RSI") and "inestable" not in e and "nada" not in e]
    print("VEREDICTO:")
    print(f"  Trenders estables (trend gana ventana tras ventana): {', '.join(estables_trend) or 'ninguno'}")
    print(f"  Reversion estables (oscila ventana tras ventana):    {', '.join(estables_rev) or 'ninguno'}")
    print("\nSi tu portafolio mixto tiene piso real, NEAR/RENDER deberian salir trenders estables")
    print("y alguna (SOL? INJ? LINK?) deberia salir reversion estable. Si todo sale 'inestable',")
    print("el caracter no persiste: era espejismo de una ventana y NO conviene armar el mixto.")


if __name__ == "__main__":
    main()
