#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_sol.py
-----------
Foco en UNA moneda: SOL/BTC, ventana reciente (~250 dias). Sin canastas.
Compara, en BTC y con fees:
  - SAR sola              (mas ganancia, mas drawdown)
  - COMBO (SAR+ST)        (tu sistema actual, mas lento)
  - Supertrend solo
  - SAR rapida (AF 0.04/0.4)  -> testea si achicar el atraso de los puntitos ayuda
  - Bollinger reversion   (oscilacion)

La pregunta de Ludo: SAR sola en SOL parecia funcionar mejor que el combo, pero
el atraso de los puntitos (entra tarde, sale dos velas pasado el giro) la arruina.
Vemos si es verdad y si una SAR mas rapida lo arregla o lo empeora.

Velas cerradas. Correr en la VPS: python3 test_sol.py
"""

import urllib.request, json, time
import numpy as np
import pandas as pd

SYMBOL = "SOLBTC"; INTERVAL = "4h"; DIAS = 250; FEE = 0.001
ST_PER, ST_MULT = 10, 3.0


def get_klines(symbol, interval, dias):
    ms = {"4h": 4*3600*1000}[interval]; n_obj = int(dias*24*3600*1000/ms)+60
    end = int(time.time()*1000); rows = []
    while len(rows) < n_obj:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        batch = json.load(urllib.request.urlopen(req, timeout=20))
        if not batch: break
        rows = batch + rows; end = batch[0][0]-1
        if len(batch) < 1000: break
        time.sleep(0.25)
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


def bt(close, hold, fee, start):
    btc,coin=1.0,0.0; pos="BTC"; entry=None; eq=[]; tramos=[]; encoin=0
    for i in range(start,len(close)):
        if pos=="COIN" and not hold[i]:
            btc=coin*close[i]*(1-fee); tramos.append(close[i]/entry-1); coin=0.0; pos="BTC"
        if pos=="BTC" and hold[i] and (i==0 or not hold[i-1]):
            coin=(btc*(1-fee))/close[i]; btc=0.0; entry=close[i]; pos="COIN"
        if pos=="COIN": encoin+=1
        eq.append(btc+coin*close[i])
    eq=pd.Series(eq); dd=(eq/eq.cummax()-1).min()*100
    wins=[t for t in tramos if t>0]; losses=[t for t in tramos if t<=0]
    return {"ret":(eq.iloc[-1]-1)*100,"dd":dd,"n":len(tramos),
            "win":(len(wins)/len(tramos)*100) if tramos else 0,
            "gan":(np.mean(wins)*100) if wins else 0,
            "perd":(np.mean(losses)*100) if losses else 0,
            "encoin":encoin/(len(close)-start)*100}


def main():
    print(f"Bajando {SYMBOL} {INTERVAL} ({DIAS} dias)...")
    df = get_klines(SYMBOL, INTERVAL, DIAS).iloc[:-1].reset_index(drop=True)
    print(f"  {len(df)} velas | {df['time'].iloc[0]} -> {df['time'].iloc[-1]}\n")
    c = df["c"]; close = c.values; start = 40

    sar_std  = sar_dir(df,0.02,0.02,0.20)
    sar_fast = sar_dir(df,0.04,0.04,0.40)
    st       = supertrend_dir(df,ST_PER,ST_MULT)
    ma,bbu,bbl = bollinger(c,20,2)

    estr = {
        "SAR sola (0.02/0.2)":  (sar_std==1).values,
        "SAR rapida (0.04/0.4)":(sar_fast==1).values,
        "Supertrend solo":      (st==1).values,
        "COMBO (SAR+ST)":       ((sar_std==1)&(st==1)).values,
        "Bollinger reversion":  reversion_signal(c<bbl, c>=ma).values,
    }

    print(f"{'estrategia':<24}{'retBTC':>8}{'maxDD':>8}{'trades':>7}{'win%':>6}{'gan.med':>8}{'perd.med':>9}{'%enSOL':>8}")
    print("-"*78)
    for nom, hold in estr.items():
        r = bt(close, hold, FEE, start)
        print(f"{nom:<24}{r['ret']:>7.2f}%{r['dd']:>7.2f}%{r['n']:>7}{r['win']:>5.0f}%"
              f"{r['gan']:>7.2f}%{r['perd']:>8.2f}%{r['encoin']:>7.1f}%")
    print("-"*78)
    print("Mira SAR sola vs COMBO: si SAR sola tiene mas retBTC pero peor maxDD, ahi esta")
    print("el trade-off que sentis (mas ganancia, mas caida). La 'SAR rapida' te dice si")
    print("achicar el atraso de los puntitos ayuda (mas ret) o lo arruina (mas whipsaw, mas trades).")


if __name__ == "__main__":
    main()
