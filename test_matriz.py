#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_matriz.py
--------------
Matriz ESTRATEGIA x MONEDA. Para cada par alt/BTC corre tres estrategias y
muestra cual le gana a cada moneda:
  - TREND     : el combo SAR+Supertrend (lo que ya usas)
  - BOLLINGER : reversion (entra bajo banda inferior, sale en la media)
  - RSI       : reversion (entra RSI14<30, sale RSI14>50)

Hipotesis a testear: las monedas que PIERDEN en trend (choppy, no trendean
vs BTC) podrian GANAR en reversion. Si es asi, la jugada no es una estrategia
para todo, sino la estrategia que le toca a cada moneda segun su caracter.

Todo en BTC, con fees, velas cerradas. Correr en la VPS: python3 test_matriz.py

OJO con el overfit: elegir "la mejor estrategia por moneda" mirando ESTA misma
data es optimista (sesgo de seleccion). Sirve para EXPLORAR el patron, no para
prometer ese retorno. Si un patron es fuerte (ej: choppy -> reversion), recien
ahi se piensa una regla estable.
"""

import urllib.request, json, time
import numpy as np
import pandas as pd

UNIVERSO = ["ETHBTC","SOLBTC","XRPBTC","ADABTC","AVAXBTC","LINKBTC","DOTBTC",
            "LTCBTC","ATOMBTC","INJBTC","NEARBTC","RENDERBTC","SUIBTC","FETBTC"]
INTERVAL = "4h"; DIAS = 250; FEE = 0.001
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
    if len(rows) < 80:
        print(f"   [skip] {symbol}: historia insuficiente"); return None
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
    btc,coin=1.0,0.0; pos="BTC"; entry=None; eq=[]; tramos=[]
    for i in range(len(close)):
        if pos=="COIN" and not hold[i]:
            btc=coin*close[i]*(1-fee); tramos.append(close[i]/entry-1); coin=0.0; pos="BTC"
        if pos=="BTC" and hold[i] and (i==0 or not hold[i-1]):
            coin=(btc*(1-fee))/close[i]; btc=0.0; entry=close[i]; pos="COIN"
        eq.append(btc+coin*close[i])
    return pd.Series(eq), tramos


def maxdd(eq): return (eq/eq.cummax()-1).min()*100


def main():
    print(f"Bajando {len(UNIVERSO)} pares {INTERVAL} ({DIAS} dias)...")
    data = {}
    for sym in UNIVERSO:
        df = get_klines(sym, INTERVAL, DIAS)
        if df is None: continue
        df = df.iloc[:-1].reset_index(drop=True)
        c = df["c"]
        combo = (supertrend_dir(df,ST_PER,ST_MULT)==1) & (sar_dir(df,SAR_AF0,SAR_STEP,SAR_MAX)==1)
        ma,bbu,bbl = bollinger(c,20,2); r14 = rsi(c,14)
        hold_boll = reversion_signal(c<bbl, c>=ma)
        hold_rsi  = reversion_signal(r14<30, r14>50)
        data[sym] = pd.DataFrame({
            "close": c.values,
            "trend": combo.fillna(False).values,
            "boll":  hold_boll.values,
            "rsi":   hold_rsi.values,
        }, index=pd.DatetimeIndex(df["time"].values))
        time.sleep(0.15)

    common = None
    for s in data.values():
        common = s.index if common is None else common.intersection(s.index)
    common = common.sort_values()[40:]
    print(f"\n{len(data)} monedas | ventana comun {len(common)} velas "
          f"({common[0].date()} -> {common[-1].date()})\n")

    res = {}
    for sym, s in data.items():
        s2 = s.loc[common]; close = s2["close"].values
        out = {}
        for strat in ["trend","boll","rsi"]:
            eq, tr = sleeve_bt(close, s2[strat].values, FEE)
            out[strat] = {"ret": (eq.iloc[-1]-1)*100, "dd": maxdd(eq), "eq": eq}
        res[sym] = out

    # matriz
    print(f"{'moneda':<11}{'TREND':>9}{'BOLL':>9}{'RSI':>9}   mejor")
    print("-"*52)
    for sym in sorted(res, key=lambda k: res[k]["trend"]["ret"], reverse=True):
        t = res[sym]["trend"]["ret"]; b = res[sym]["boll"]["ret"]; r = res[sym]["rsi"]["ret"]
        mejor = max([("TREND",t),("BOLL",b),("RSI",r)], key=lambda kv: kv[1])
        print(f"{sym:<11}{t:>8.1f}%{b:>8.1f}%{r:>8.1f}%   {mejor[0]} ({mejor[1]:.0f}%)")
    print("-"*52)

    # test directo de la hipotesis: monedas que PIERDEN en trend, en reversion Bollinger
    perdedoras_trend = [s for s in res if res[s]["trend"]["ret"] <= 0]
    if perdedoras_trend:
        eqs = [res[s]["boll"]["eq"].reset_index(drop=True) for s in perdedoras_trend]
        canasta_rev = pd.concat(eqs, axis=1).mean(axis=1)
        print(f"\nHIPOTESIS: las {len(perdedoras_trend)} que pierden en TREND, corridas en BOLLINGER:")
        print(f"  canasta reversion de esas perdedoras: ret {(canasta_rev.iloc[-1]-1)*100:.2f}%  maxDD {maxdd(canasta_rev):.2f}%")
        gan = [s for s in perdedoras_trend if res[s]["boll"]["ret"] > 0]
        print(f"  de esas {len(perdedoras_trend)}, dan POSITIVO en Bollinger: {len(gan)} -> {', '.join(gan) if gan else 'ninguna'}")

    # referencia: canasta trend de las ganadoras
    ganadoras = [s for s in res if res[s]["trend"]["ret"] > 0]
    if ganadoras:
        eqs = [res[s]["trend"]["eq"].reset_index(drop=True) for s in ganadoras]
        canasta_gan = pd.concat(eqs, axis=1).mean(axis=1)
        print(f"\nReferencia: canasta TREND de las {len(ganadoras)} ganadoras ({', '.join(ganadoras)}):")
        print(f"  ret {(canasta_gan.iloc[-1]-1)*100:.2f}%  maxDD {maxdd(canasta_gan):.2f}%")

    print("\nQue buscar: si varias perdedoras-de-trend dan claramente positivo en BOLL,")
    print("tu hipotesis se sostiene y tiene sentido un portafolio mixto (trend a las que")
    print("trendean, reversion a las que oscilan). Si casi ninguna, la reversion sigue")
    print("sin sobrevivir a las fees y lo mejor es quedarte con trend en NEAR+RENDER(+ATOM).")


if __name__ == "__main__":
    main()
