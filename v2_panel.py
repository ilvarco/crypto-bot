#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
#  v2_panel.py  -  Panel web del bot SAR (NEAR/BTC)
# ----------------------------------------------------------------------------
#  Lee el log del bot en SOLO LECTURA y sirve una pagina con:
#    - Escalera SAR (4h..1d), calculada en el navegador desde Binance publico
#    - Estado real del bot (largo en NEAR / refugio en BTC)
#    - Tabla de trades confirmados
#    - Grafico de acumulacion de BTC (cuanto BTC junta vuelta a vuelta)
#
#  NO toca el bot ni la cuenta. Sin claves. Solo stdlib.
#  Corre como servicio en el puerto 8000  ->  http://167.233.48.213:8000
# ============================================================================

import json, re, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG  = "/root/bot/v2_NEARBTC_log.txt"
PORT = 8000

RE_STATE = re.compile(
    r'^(\S+ \S+) \[(\w+)\] \S+ vela cerrada \| SAR=(\w+) \| tengo (\S+) '
    r'\(NEAR=([\d.]+) ~([\d.]+)BTC, BTC=([\d.]+)\)')
RE_SELL = re.compile(
    r'^(\S+ \S+) \[\w+\] \S+ OK VENTA confirmada: NEAR ([\d.]+)->([\d.]+) '
    r'\| BTC ([\d.]+)->([\d.]+)')
RE_BUY = re.compile(
    r'^(\S+ \S+) \[\w+\] \S+ OK COMPRA confirmada: BTC ([\d.]+)->([\d.]+) '
    r'\| NEAR ([\d.]+)->([\d.]+)')
RE_HALT = re.compile(r'FRENO DE MANO ACTIVADO')

def parse_log():
    trades = []; last_state = None; halted = False
    if os.path.exists(LOG):
        with open(LOG, errors="ignore") as f:
            for line in f:
                m = RE_SELL.match(line)
                if m:
                    trades.append({"ts": m.group(1), "side": "VENTA",
                        "near_before": float(m.group(2)), "near_after": float(m.group(3)),
                        "btc_before": float(m.group(4)), "btc_after": float(m.group(5))})
                    continue
                m = RE_BUY.match(line)
                if m:
                    trades.append({"ts": m.group(1), "side": "COMPRA",
                        "btc_before": float(m.group(2)), "btc_after": float(m.group(3)),
                        "near_before": float(m.group(4)), "near_after": float(m.group(5))})
                    continue
                m = RE_STATE.match(line)
                if m:
                    last_state = {"ts": m.group(1), "sar": m.group(3), "pos": m.group(4),
                        "near": float(m.group(5)), "near_btc": float(m.group(6)),
                        "btc": float(m.group(7))}
                    continue
                if RE_HALT.search(line):
                    halted = True
    # curva de acumulacion: BTC en mano tras cada VENTA (cada vuelta a refugio)
    acc = [{"ts": t["ts"], "btc": t["btc_after"]} for t in trades if t["side"] == "VENTA"]
    return {"trades": trades, "state": last_state, "acc": acc, "halted": halted}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/datos"):
            body = json.dumps(parse_log()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

PAGE = r"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel SAR · NEAR/BTC</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg-top:#081019;--bg-bot:#03070c;--panel:#0c1722;--panel-2:#0a131c;
  --line:#16242f;--text:#cfe0ec;--dim:#5d7488;--faint:#3a4c5c;
  --green:#34d27b;--green-deep:#0f3322;--red:#ff5d57;--red-deep:#3a1414;--gold:#e7b54a;
  --mono:'JetBrains Mono','SF Mono',ui-monospace,Menlo,Consolas,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{font-family:var(--mono);color:var(--text);
  background:linear-gradient(180deg,var(--bg-top),var(--bg-bot));background-attachment:fixed;
  min-height:100%;-webkit-font-smoothing:antialiased;padding:28px 18px 44px;display:flex;justify-content:center}
.wrap{width:100%;max-width:560px}
header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;
  margin-bottom:20px;padding-bottom:14px;border-bottom:1px solid var(--line)}
.pair{font-size:26px;font-weight:800;letter-spacing:.04em}
.pair small{color:var(--dim);font-weight:500;font-size:13px;letter-spacing:.12em;text-transform:uppercase}
.sub{font-size:11px;color:var(--dim);font-weight:500;letter-spacing:.12em;text-transform:uppercase;margin-top:4px}
.updated{font-size:11px;color:var(--dim);text-align:right;line-height:1.6;letter-spacing:.04em}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);margin-right:6px;
  vertical-align:middle;animation:pulse 2.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(52,210,123,.5)}70%{box-shadow:0 0 0 7px rgba(52,210,123,0)}100%{box-shadow:0 0 0 0 rgba(52,210,123,0)}}
.section-h{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--dim);margin:26px 4px 11px}
.section-h:first-of-type{margin-top:0}

.banner{border:1px solid var(--line);border-radius:12px;padding:20px 22px;position:relative;overflow:hidden;background:var(--panel)}
.banner .eyebrow{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--dim);margin-bottom:8px}
.banner .state{font-size:30px;font-weight:800;letter-spacing:.02em;line-height:1.05}
.banner .note{font-size:12px;color:var(--dim);margin-top:10px}
.banner.green{background:linear-gradient(180deg,rgba(52,210,123,.10),var(--panel));border-color:rgba(52,210,123,.35)}
.banner.green .state{color:var(--green)}
.banner.red{background:linear-gradient(180deg,rgba(255,93,87,.09),var(--panel));border-color:rgba(255,93,87,.32)}
.banner.red .state{color:var(--red)}
.banner .edge{position:absolute;left:0;top:0;bottom:0;width:4px}
.banner.green .edge{background:var(--green)}.banner.red .edge{background:var(--red)}

.halt{margin-top:10px;border:1px solid var(--red);background:var(--red-deep);border-radius:8px;
  padding:10px 14px;font-size:12px;color:var(--red);letter-spacing:.02em}

.cards{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.card{border:1px solid var(--line);border-radius:10px;background:var(--panel-2);padding:13px 14px}
.card .k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);margin-bottom:7px}
.card .v{font-size:17px;font-weight:800;letter-spacing:.01em}
.card .v.gold{color:var(--gold)}.card .v.green{color:var(--green)}.card .v.red{color:var(--red)}
.card .v small{font-size:11px;color:var(--dim);font-weight:500}

.chartbox{border:1px solid var(--line);border-radius:12px;background:var(--panel-2);padding:16px}
.chartbox .cap{font-size:11px;color:var(--dim);margin-bottom:6px;letter-spacing:.02em}
.empty{color:var(--faint);font-size:12px;text-align:center;padding:24px 0;letter-spacing:.02em}

table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:var(--dim);font-weight:500;font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  padding:0 8px 8px;border-bottom:1px solid var(--line)}
td{padding:9px 8px;border-bottom:1px solid var(--panel)}
tr:last-child td{border-bottom:none}
.tag{font-weight:700;font-size:11px;letter-spacing:.04em}
.tag.s{color:var(--red)}.tag.b{color:var(--green)}
.num{color:var(--gold)}
td.t{color:var(--dim);font-size:11px}

.ladder{display:flex;flex-direction:column;gap:8px}
.rung{display:grid;grid-template-columns:54px 76px 1fr;align-items:center;gap:14px;padding:13px 16px;
  border:1px solid var(--line);border-radius:10px;background:var(--panel-2)}
.rung.bind{border-color:rgba(231,181,74,.4);background:var(--panel);box-shadow:inset 0 0 0 1px rgba(231,181,74,.07)}
.tf{font-weight:800;font-size:16px}.tf .crown{display:block;font-size:9px;letter-spacing:.14em;color:var(--gold);font-weight:600;margin-top:2px}
.chip{font-size:12px;font-weight:700;letter-spacing:.06em;padding:5px 0;text-align:center;border-radius:6px}
.chip.green{color:var(--green);background:rgba(52,210,123,.12)}.chip.red{color:var(--red);background:rgba(255,93,87,.11)}.chip.na{color:var(--faint);background:#0b141d}
.meter{display:flex;flex-direction:column;gap:5px}
.meter .lbl{display:flex;justify-content:space-between;font-size:11px;color:var(--dim)}
.meter .lbl em{font-style:normal}.meter .lbl .flip{color:var(--gold)}
.bar{height:6px;border-radius:4px;background:#0a141d;overflow:hidden}
.bar i{display:block;height:100%;border-radius:4px;transition:width .5s ease}
.bar i.green{background:linear-gradient(90deg,var(--green-deep),var(--green))}
.bar i.red{background:linear-gradient(90deg,var(--red-deep),var(--red))}
.thin{color:var(--red)!important;font-weight:700}
.rule{margin-top:24px;padding-top:16px;border-top:1px solid var(--line);font-size:11.5px;color:var(--dim);line-height:1.85}
.rule b{color:var(--text);font-weight:700}.rule .g{color:var(--green)}.rule .r{color:var(--red)}
.err{color:var(--red);font-size:11px;margin-top:8px;text-align:center;min-height:14px}
</style></head>
<body><div class="wrap">
  <header>
    <div><div class="pair">NEAR<small> / btc</small></div><div class="sub">panel sar · 0.02 / 0.2</div></div>
    <div class="updated" id="updated">conectando…</div>
  </header>

  <div class="banner" id="banner"><div class="edge"></div>
    <div class="eyebrow">señal 4h · lo que define al bot</div>
    <div class="state" id="bannerState">—</div>
    <div class="note" id="bannerNote">leyendo…</div>
  </div>
  <div id="haltbox"></div>

  <div class="section-h">tu bot ahora</div>
  <div class="cards" id="cards"></div>

  <div class="section-h">acumulación de btc</div>
  <div class="chartbox"><div class="cap" id="accCap">btc en mano tras cada vuelta a refugio</div>
    <div id="accChart"></div></div>

  <div class="section-h">trades confirmados</div>
  <div id="tradesBox"></div>

  <div class="section-h">escalera sar · contexto</div>
  <div class="ladder" id="ladder"></div>
  <div class="err" id="err"></div>

  <div class="rule">
    <b>Cómo opera.</b> Solo tiene NEAR mientras el <b>4H está en <span class="g">verde</span></b>.
    Cuando gira a <span class="r">rojo</span>, vende y vuelve a BTC, y ahí espera — no opera en rojo, es refugio.
    Es <b>largo solo en verde</b>: la venta en rojo es la salida, no una operación nueva.<br>
    Los marcos lentos (6H→1D) son contexto: si el 4H gira pero abajo siguen firmes, suele ser sacudón, no reversa.
    Todo sobre la <b>vela cerrada</b> (lo mismo que mira el bot).
  </div>
</div>

<script>
const PAIR="NEARBTC", STEP=0.02, MX=0.2, GAUGE_MAX=8;
const TFS=[{tf:"4h",bind:true},{tf:"6h"},{tf:"8h"},{tf:"12h"},{tf:"1d"}];

function psar(H,L,step,mx){
  const n=H.length,up=new Array(n).fill(true),sa=new Array(n).fill(0);
  let tr=true,af=step,ep=H[0],sar=L[0];sa[0]=sar;
  for(let i=1;i<n;i++){let s=sar+af*(ep-sar);
    if(tr){s=Math.min(s,L[i-1],i>=2?L[i-2]:L[i-1]);
      if(L[i]<s){tr=false;s=ep;ep=L[i];af=step;}else if(H[i]>ep){ep=H[i];af=Math.min(af+step,mx);}}
    else{s=Math.max(s,H[i-1],i>=2?H[i-2]:H[i-1]);
      if(H[i]>s){tr=true;s=ep;ep=H[i];af=step;}else if(L[i]<ep){ep=L[i];af=Math.min(af+step,mx);}}
    sar=s;up[i]=tr;sa[i]=sar;}
  return {up,sa};
}
async function klines(tf){
  const r=await fetch(`https://api.binance.com/api/v3/klines?symbol=${PAIR}&interval=${tf}&limit=400`);
  if(!r.ok) throw new Error("HTTP "+r.status); return r.json();
}
function analyze(raw){
  const conf=raw.slice(0,-1);
  const H=conf.map(k=>+k[2]),L=conf.map(k=>+k[3]),C=conf.map(k=>+k[4]);
  const r=psar(H,L,STEP,MX),i=r.up.length-1;
  const Hf=raw.map(k=>+k[2]),Lf=raw.map(k=>+k[3]),rf=psar(Hf,Lf,STEP,MX);
  return {upConf:r.up[i],sarConf:r.sa[i],closeConf:C[i],upProv:rf.up[rf.up.length-1]};
}
const fmtPx=p=>p.toFixed(8).replace(/0+$/,"").replace(/\.$/,".0");
const fmtBtc=b=>b.toFixed(8);

// ---- escalera SAR (Binance) ----
async function ladder(){
  const errEl=document.getElementById("err");errEl.textContent="";
  let price=null,anyErr=false,four=null;const rows=[];
  for(const t of TFS){
    try{const raw=await klines(t.tf);const a=analyze(raw);
      if(t.bind)four=a; if(price===null)price=+raw[raw.length-1][4];
      rows.push({...t,...a,ok:true});}
    catch(e){rows.push({...t,ok:false});anyErr=true;}
  }
  const banner=document.getElementById("banner"),bs=document.getElementById("bannerState"),bn=document.getElementById("bannerNote");
  if(four){
    if(four.upConf){banner.className="banner green";bs.textContent="EL BOT EN NEAR";bn.textContent="4H en verde · largo, montado a la tendencia";}
    else{banner.className="banner red";bs.textContent="EL BOT EN BTC";bn.textContent="4H en rojo · refugio en BTC, fuera de NEAR";}
    if(four.upProv!==four.upConf)bn.textContent+=` · ⟳ la vela 4H en curso ya está en ${four.upProv?"VERDE":"ROJO"}`;
  }
  const lad=document.getElementById("ladder");lad.innerHTML="";
  for(const row of rows){
    const div=document.createElement("div");div.className="rung"+(row.bind?" bind":"");
    const crown=row.bind?'<span class="crown">4h · manda</span>':'';
    if(!row.ok){div.innerHTML=`<div class="tf">${row.tf.toUpperCase()}${crown}</div><div class="chip na">s/d</div><div class="meter"><div class="lbl"><em>sin datos</em></div><div class="bar"></div></div>`;lad.appendChild(div);continue;}
    const g=row.upConf,pct=Math.abs(price-row.sarConf)/price*100,w=Math.max(3,Math.min(pct/GAUGE_MAX,1)*100),thin=pct<1;
    const lbl=g?`colchón <em>${pct.toFixed(2)}%</em>`:`a verde <em>${pct.toFixed(2)}%</em>`;
    let flip=""; if(row.upProv!==row.upConf)flip=`<span class="flip">⟳ en curso ${row.upProv?'verde':'rojo'}</span>`;
    div.innerHTML=`<div class="tf">${row.tf.toUpperCase()}${crown}</div><div class="chip ${g?'green':'red'}">${g?'VERDE':'ROJO'}</div>
      <div class="meter"><div class="lbl"><span class="${thin?'thin':''}">${lbl}</span>${flip}</div>
      <div class="bar"><i class="${g?'green':'red'}" style="width:${w}%"></i></div></div>`;
    lad.appendChild(div);
  }
  if(anyErr)errEl.textContent="algunos marcos no cargaron (red) — reintenta solo";
}

// ---- bot: estado, trades, acumulacion (desde /datos) ----
function svgChart(acc){
  if(acc.length===0) return '<div class="empty">todavía no hay ventas registradas — la curva se llena con cada vuelta a refugio</div>';
  if(acc.length===1) return `<div class="empty">1ª vuelta registrada: <b style="color:var(--gold)">${fmtBtc(acc[0].btc)} BTC</b><br>la curva se dibuja a partir de la 2ª vuelta</div>`;
  const W=512,Hh=150,pad=8,vals=acc.map(a=>a.btc);
  let mn=Math.min(...vals),mx=Math.max(...vals);if(mn===mx){mn*=0.999;mx*=1.001;}
  const x=i=>pad+i*(W-2*pad)/(acc.length-1);
  const y=v=>pad+(1-(v-mn)/(mx-mn))*(Hh-2*pad);
  let pts=acc.map((a,i)=>`${x(i).toFixed(1)},${y(a.btc).toFixed(1)}`).join(" ");
  let dots=acc.map((a,i)=>`<circle cx="${x(i).toFixed(1)}" cy="${y(a.btc).toFixed(1)}" r="3" fill="#e7b54a"/>`).join("");
  const area=`${pad},${Hh-pad} ${pts} ${(W-pad)},${Hh-pad}`;
  return `<svg viewBox="0 0 ${W} ${Hh}" width="100%" preserveAspectRatio="none" style="display:block">
    <polygon points="${area}" fill="rgba(231,181,74,.08)"/>
    <polyline points="${pts}" fill="none" stroke="#e7b54a" stroke-width="2"/>${dots}</svg>`;
}
async function botData(){
  let d; try{d=await (await fetch("/datos")).json();}catch(e){return;}
  // banner: si el log dice algo del estado y el 4H no cargo, igual lo mostramos abajo
  const cards=document.getElementById("cards"),st=d.state;
  const sells=d.acc;
  const pos = st ? (st.pos.indexOf("NEAR")>=0 ? "NEAR" : "BTC") : "—";
  let posV = pos==="NEAR" ? `<span class="v green">NEAR</span>` : (pos==="BTC"?`<span class="v gold">BTC</span>`:`<span class="v">—</span>`);
  const btcNow = st ? (pos==="NEAR" ? st.btc : st.btc) : 0;
  cards.innerHTML=`
    <div class="card"><div class="k">posición</div><div class="v ${pos==='NEAR'?'green':'gold'}">${pos}</div></div>
    <div class="card"><div class="k">vueltas</div><div class="v">${sells.length}</div></div>
    <div class="card"><div class="k">btc en mano</div><div class="v gold">${st?(+st.btc).toFixed(6):'—'}</div></div>`;
  // acumulacion stats + chart
  const cap=document.getElementById("accCap");
  if(sells.length>=2){
    const first=sells[0].btc,last=sells[sells.length-1].btc,chg=(last/first-1)*100;
    cap.innerHTML=`1ª venta <b style="color:var(--gold)">${fmtBtc(first)}</b> → última <b style="color:var(--gold)">${fmtBtc(last)}</b> · <span style="color:${chg>=0?'var(--green)':'var(--red)'}">${chg>=0?'+':''}${chg.toFixed(2)}%</span>`;
  } else { cap.textContent="btc en mano tras cada vuelta a refugio"; }
  document.getElementById("accChart").innerHTML=svgChart(sells);
  // trades
  const tb=document.getElementById("tradesBox");
  if(d.trades.length===0){tb.innerHTML='<div class="empty">sin trades todavía</div>';}
  else{
    const rows=d.trades.slice().reverse().map(t=>{
      const isSell=t.side==="VENTA";
      const detail = isSell
        ? `${t.near_before.toFixed(1)} NEAR → <span class="num">${(+t.btc_after).toFixed(6)}</span> BTC`
        : `<span class="num">${(+t.btc_before).toFixed(6)}</span> BTC → ${t.near_after.toFixed(1)} NEAR`;
      return `<tr><td class="t">${t.ts.slice(5,16)}</td><td><span class="tag ${isSell?'s':'b'}">${t.side}</span></td><td>${detail}</td></tr>`;
    }).join("");
    tb.innerHTML=`<table><thead><tr><th>fecha</th><th>tipo</th><th>detalle</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  // halt
  const hb=document.getElementById("haltbox");
  hb.innerHTML = d.halted ? '<div class="halt">⚠ FRENO DE MANO activo: el bot detectó una orden que no reconcilió y dejó de operar. Revisá el server.</div>' : '';
}

async function refresh(){
  await Promise.all([ladder(), botData()]);
  const now=new Date();
  document.getElementById("updated").innerHTML=`<span class="dot"></span>actualizado ${now.toLocaleTimeString('es-AR')}<br>refresca cada 60s`;
}
refresh(); setInterval(refresh,60000);
</script>
</body></html>"""

if __name__ == "__main__":
    print(f"panel en http://0.0.0.0:{PORT}  (lee {LOG})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
