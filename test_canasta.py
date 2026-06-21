#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_canasta.py
---------------
Corre el MISMO combo (SAR+Supertrend) sobre una canasta de pares alt/BTC en 4H.
Modelo realista: capital partido en partes iguales, una "manga" por moneda.
Cada manga entra a su moneda cuando su combo esta verde, vuelve a BTC cuando rojo,
independiente de las demas. La canasta = promedio de las mangas (equiponderado).

Idea: el edge del trend-following esta en la AMPLITUD. Mientras NEAR lateraliza,
otras monedas trendean. Esto deberia bajar el drawdown y los tiempos muertos
(menos "oportunidades desperdiciadas") sin tocar el combo.

Compara: cada moneda sola vs la canasta entera vs NEAR sola (tu sistema actual).
Todo en BTC, con fees, velas cerradas. Correr en la VPS: python3 test_canasta.py
"""

import urllib.request, json, time
import numpy as np
import pandas as pd

# ------------------ universo (editable) ------------------
UNIVERSO = ["ETHBTC","SOLBTC","XRPBTC","ADABTC","AVAXBTC","LINKBTC","DOTBTC",
            "LTCBTC","ATOMBTC","INJBTC","NEARBTC","RENDERBTC","SUIBTC","FETBTC"]
INTERVAL = "4h"
DIAS     = 250
FEE      = 0.001
ST_PER, ST_MULT = 10, 3.0
SAR_AF0, SAR_STEP, SAR_MAX = 0.02, 0.02, 0.20
BENCH    = "NEARBTC"        # benchmark = tu sistema actual
# ---------------------------------------------------------


def get_klines(symbol, interval, dias):
    ms = {"4h": 4*3600*1000}[interval]
    n_obj = int(dias*24*3600*1000/ms) + 60
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
        print(f"   [skip] {symbol}: {e}")
        return None
    if len(rows) < 80:
        print(f"   [skip] {symbol}: historia insuficiente ({len(rows)})")
        return None
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


def sleeve_bt(close, combo, fee):
    """Una manga: en la moneda cuando combo verde, en BTC cuando rojo. Equity en BTC desde 1.0."""
    btc, coin = 1.0, 0.0; pos = "BTC"; entry = None
    eq = []; tramos = []
    n = len(close)
    for i in range(n):
        if pos == "COIN" and not combo[i]:
            btc = coin*close[i]*(1-fee); tramos.append(close[i]/entry-1); coin = 0.0; pos = "BTC"
        if pos == "BTC" and combo[i] and (i == 0 or not combo[i-1]):
            coin = (btc*(1-fee))/close[i]; btc = 0.0; entry = close[i]; pos = "COIN"
        eq.append(btc + coin*close[i])
    return pd.Series(eq), tramos


def maxdd(eq):
    return (eq/eq.cummax() - 1).min() * 100


def main():
    print(f"Bajando {len(UNIVERSO)} pares {INTERVAL} ({DIAS} dias)...")
    data = {}
    for sym in UNIVERSO:
        df = get_klines(sym, INTERVAL, DIAS)
        if df is None: continue
        df = df.iloc[:-1].reset_index(drop=True)              # descarta vela en formacion
        combo = (supertrend_dir(df, ST_PER, ST_MULT) == 1) & (sar_dir(df, SAR_AF0, SAR_STEP, SAR_MAX) == 1)
        s = pd.DataFrame({"close": df["c"].values, "combo": combo.fillna(False).values},
                         index=pd.DatetimeIndex(df["time"].values))
        data[sym] = s
        time.sleep(0.15)

    if len(data) < 2:
        print("No bajaron suficientes monedas."); return

    # ventana comun (interseccion de timestamps)
    common = None
    for s in data.values():
        common = s.index if common is None else common.intersection(s.index)
    common = common.sort_values()[40:]                       # corto warmup
    print(f"\nMonedas cargadas: {len(data)} | ventana comun: {len(common)} velas "
          f"({common[0].date()} -> {common[-1].date()})\n")

    # backtest por manga sobre la ventana comun
    sleeves = {}
    any_green = np.zeros(len(common), dtype=int)
    for sym, s in data.items():
        s2 = s.loc[common]
        eq, tramos = sleeve_bt(s2["close"].values, s2["combo"].values, FEE)
        sleeves[sym] = {"eq": eq, "tramos": tramos, "combo": s2["combo"].values}
        any_green += s2["combo"].values.astype(int)

    # tabla por moneda
    print(f"{'moneda':<11}{'retBTC':>9}{'maxDD':>8}{'trades':>8}{'win%':>6}{'%verde':>8}")
    print("-"*50)
    rows = []
    for sym, d in sorted(sleeves.items(), key=lambda kv: kv[1]["eq"].iloc[-1], reverse=True):
        eq = d["eq"]; tr = d["tramos"]
        ret = (eq.iloc[-1]-1)*100; dd = maxdd(eq)
        win = (np.mean([t > 0 for t in tr])*100) if tr else 0.0
        green = d["combo"].mean()*100
        rows.append((sym, ret, dd))
        print(f"{sym:<11}{ret:>8.2f}%{dd:>7.2f}%{len(tr):>8}{win:>5.0f}%{green:>7.1f}%")
    print("-"*50)

    # canasta equiponderada
    basket = pd.concat([d["eq"].reset_index(drop=True) for d in sleeves.values()], axis=1).mean(axis=1)
    bret = (basket.iloc[-1]-1)*100; bdd = maxdd(basket)

    near = sleeves.get(BENCH)
    nret = (near["eq"].iloc[-1]-1)*100 if near else float("nan")
    ndd = maxdd(near["eq"]) if near else float("nan")

    pct_basket_con_trend = np.mean(any_green > 0)*100
    pct_near_verde = near["combo"].mean()*100 if near else float("nan")

    print(f"\n{'CANASTA (14 equipond.)':<24}{bret:>8.2f}%  maxDD {bdd:>7.2f}%")
    print(f"{'NEAR sola (tu sistema)':<24}{nret:>8.2f}%  maxDD {ndd:>7.2f}%")
    print("-"*50)
    print(f"Tiempo con AL MENOS una moneda en trend (canasta): {pct_basket_con_trend:.1f}%")
    print(f"Tiempo con NEAR en trend (sola):                   {pct_near_verde:.1f}%")
    print("\nLectura:")
    print(" - Si la CANASTA tiene menor maxDD que NEAR sola y retBTC parecido o mejor,")
    print("   la diversificacion te suaviza la curva: ganas igual pero dependes menos del pump.")
    print(" - La tabla por moneda te dice cuales recortar: las de retBTC negativo o muy bajo")
    print("   estan arrastrando. Un universo curado (solo las buenas) deberia rendir mas.")
    print(" - 'Tiempo con al menos una en trend' alto = menos oportunidades desperdiciadas.")


if __name__ == "__main__":
    main()
