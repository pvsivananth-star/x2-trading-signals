"""Stdlib, localhost-only, read-only HTTP API and dashboard."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def utc_json(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        return utc_json(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): utc_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [utc_json(v) for v in value]
    return str(value) if type(value).__name__ == "Decimal" else value


class ReadAPI:
    def __init__(self, data: Mapping[str, Iterable[object]] | None = None, token: str | None = None,
                 store: object | None = None, actor: object | None = None) -> None:
        self.data = {key: tuple(values) for key, values in (data or {}).items()}
        self.token = token if token is not None else os.getenv("X2_API_TOKEN") or None
        self.store = store
        self.actor = actor

    def get(self, path: str, query: Mapping[str, list[str]] | None = None) -> tuple[int, object]:
        if path in {"/", "/dashboard", "/dashboard/"}:
            return 200, {"dashboard": "/v1/dashboard"}
        if path in {"/health", "/v1/health"}:
            synthetic = bool(self.store and any(c.synthetic for s in getattr(self.store.config, "symbols", ())
                                                for c in self.store.bars(s.symbol, "1m", 1)))
            return 200, {"status": "ok", "live_write_enabled": False,
                         "feeds_enabled": bool(self.store), "synthetic_fallback": synthetic}
        if path == "/v1/dashboard":
            return 200, {"template": "read_only_market_dashboard", "endpoints": [
                "/v1/health", "/v1/markets", "/v1/symbols", "/v1/bars", "/v1/signals",
                "/v1/orders", "/v1/fills", "/v1/positions", "/v1/pnl", "/v1/backtests"
            ]}
        if path == "/v1/markets" and self.store:
            grouped: dict[str, list[dict[str, object]]] = {}
            for item in self.store.market_symbols():
                grouped.setdefault(str(item["market"]), []).append(item)
            return 200, {"items": [{"market": k, "symbols": v} for k, v in grouped.items()]}
        if path == "/v1/symbols" and self.store:
            return 200, {"items": utc_json(self.store.market_symbols())}
        if path == "/v1/bars":
            if not self.store:
                values = list(self.data.get("bars", ()))
            else:
                params = query or {}
                symbol = params.get("symbol", [""])[0]
                interval = params.get("interval", ["1m"])[0]
                try:
                    limit = min(max(int(params.get("limit", ["100"])[0]), 1), 1000)
                except (ValueError, IndexError):
                    return 400, {"error": "invalid_pagination"}
                if not symbol:
                    return 400, {"error": "symbol_required"}
                candles = self.store.bars(symbol, interval, limit)
                values = [asdict(c) for c in candles]
            return 200, {"items": utc_json(values), "synthetic": bool(values and all(isinstance(v, Mapping) and v.get("synthetic") for v in values))}
        if path == "/v1/signals":
            if self.actor:
                values = list(getattr(self.actor, "cards", {}).values())
            else:
                values = list(self.data.get("signals", ()))
            return 200, {"items": utc_json(values), "total": len(values)}
        contract = {
            "/v1/ingestion": "ingestion", "/v1/quality": "quality",
            "/v1/orders": "orders", "/v1/fills": "fills",
            "/v1/positions": "positions", "/v1/pnl": "pnl", "/v1/backtests": "backtests",
        }
        key = contract.get(path)
        if key is None:
            return 404, {"error": "not_found"}
        values = list(self.data.get(key, ()))
        params = query or {}
        try:
            limit = min(max(int(params.get("limit", ["100"])[0]), 1), 1000)
            offset = max(int(params.get("offset", ["0"])[0]), 0)
        except (ValueError, IndexError):
            return 400, {"error": "invalid_pagination"}
        page = values[offset:offset + limit]
        if key == "pnl":
            page = page or list(self.data.get("pnl", ()))
        return 200, {"items": utc_json(page), "limit": limit, "offset": offset, "total": len(values)}

    def authorized(self, client_host: str, supplied_token: str | None) -> bool:
        if client_host not in {"127.0.0.1", "::1", "localhost"}:
            return False
        if self.token is None:
            return True
        return supplied_token == self.token


def make_handler(api: ReadAPI) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "X2ReadOnly/1.0"

        def _send(self, code: int, payload: object, content_type: str = "application/json") -> None:
            body = (payload if isinstance(payload, bytes) else json.dumps(payload, sort_keys=True).encode())
            if content_type.startswith("text/html"):
                body = payload if isinstance(payload, bytes) else str(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            host = self.client_address[0]
            supplied = self.headers.get("X2_API_TOKEN")
            if not api.authorized(host, supplied):
                self._send(401, {"error": "unauthorized"})
                return
            parsed = urlparse(self.path)
            if parsed.path in {"/dashboard", "/v1/dashboard", "/"}:
                content = """<!doctype html><html><head><meta charset='utf-8'><title>X2 Market Dashboard</title>
<style>body{margin:0;font-family:system-ui;background:#0b1020;color:#dfe7ff}aside{position:fixed;width:220px;height:100%;overflow:auto;background:#11172a;border-right:1px solid #26304a;padding:16px}.market{margin:14px 0 6px;color:#91a4cc;font-size:.8rem;text-transform:uppercase}.symbol{display:block;width:100%;border:0;border-radius:6px;margin:3px 0;padding:8px;text-align:left;background:transparent;color:#dfe7ff;cursor:pointer}.symbol.active{background:#263d72}main{margin-left:252px;padding:16px}canvas{width:100%;height:560px;background:#090d18;border:1px solid #26304a}.meta{display:flex;gap:18px;color:#aab7d2}.controls{margin:10px 0}.controls button{margin-right:4px}.badge{display:inline-block;margin-right:8px;border-radius:4px;padding:4px 8px;font-weight:700}.buy{background:#166b54;color:#baffea}.sell{background:#82343d;color:#ffd1d5}.hold{background:#39445e;color:#dfe7ff}.reason{margin:10px 0;color:#dfe7ff}.params{color:#91a4cc;font-size:.85rem}.synthetic{color:#ff9f43}</style></head>
<body><aside><h2>X2 Markets</h2><div id='markets'></div></aside><main><h1 id='title'>Select a symbol</h1>
<div class='controls'><label>Timeframe </label><span id='tfs'></span></div><div id='analysis'></div><div class='meta' id='meta'></div>
<canvas id='chart'></canvas><div id='notice'></div></main><script>
let active=null,interval='1m';
async function markets(){const r=await fetch('/v1/markets');const d=await r.json();const box=document.getElementById('markets');box.innerHTML='';for(const m of d.items||[]){const h=document.createElement('div');h.className='market';h.textContent=m.market;box.append(h);for(const s of m.symbols){const b=document.createElement('button');b.className='symbol';b.textContent=s.symbol;b.onclick=()=>load(s.symbol,s.timeframes||[]);box.append(b)}}}
function sma(values,n){return values.map((_,i)=>i<n-1?null:values.slice(i-n+1,i+1).reduce((a,b)=>a+b,0)/n)}
async function load(symbol,timeframes){active=symbol;if(!timeframes.includes(interval))interval=timeframes.includes('1m')?'1m':timeframes[0];document.querySelectorAll('.symbol').forEach(b=>b.classList.toggle('active',b.textContent===symbol));const spans=document.getElementById('tfs');spans.innerHTML='';for(const tf of timeframes||['1m','5m','15m','30m','1d']){const b=document.createElement('button');b.textContent=tf;b.onclick=()=>{interval=tf;load(symbol,timeframes)};spans.append(b)}await draw()}
async function draw(){if(!active)return;const r=await fetch('/v1/bars?symbol='+encodeURIComponent(active)+'&interval='+interval+'&limit=300');const d=await r.json();const bars=(d.items||[]).map(v=>({t:new Date(v.bar_start),o:+v.open,h:+v.high,l:+v.low,c:+v.close,v:+v.volume}));document.getElementById('title').textContent=active+' · '+interval;document.getElementById('notice').innerHTML=d.synthetic?'<span class="synthetic">SYNTHETIC TEST FIXTURE — not live market data</span>':'';const sr=await fetch('/v1/signals');const sd=await sr.json();const card=(sd.items||[]).find(v=>v.symbol===active);if(card){const action=(card.display_action||'HOLD').toLowerCase();document.getElementById('analysis').innerHTML='<span class="badge '+action+'">'+(card.display_action||'HOLD')+'</span><div class="reason">'+(card.reason||'No rule explanation')+'</div><div class="params">'+Object.entries(card.parameters||{}).map(([k,v])=>k+'='+v).join(' · ')+'</div>'}else{document.getElementById('analysis').innerHTML=''}if(!bars.length){document.getElementById('meta').textContent='No bars';return}drawChart(bars)}
function drawChart(bars){const canvas=document.getElementById('chart'),ctx=canvas.getContext('2d'),dpr=window.devicePixelRatio||1,rect=canvas.getBoundingClientRect();canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;ctx.scale(dpr,dpr);const w=rect.width,h=rect.height,left=45,right=20,top=20,bottom=30,pw=w-left-right,ph=h-top-bottom;ctx.clearRect(0,0,w,h);ctx.fillStyle='#090d18';ctx.fillRect(0,0,w,h);const lows=bars.map(b=>b.l),highs=bars.map(b=>b.h),min=Math.min(...lows),max=Math.max(...highs),pad=(max-min)*.08||1,lo=min-pad,hi=max+pad;const x=i=>left+i*pw/(bars.length-1||1),y=v=>top+(hi-v)*ph/(hi-lo);ctx.strokeStyle='#26304a';ctx.lineWidth=1;ctx.font='11px system-ui';ctx.fillStyle='#aab7d2';for(let i=0;i<5;i++){const yy=top+i*ph/4,val=hi-i*(hi-lo)/4;ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();ctx.fillText(val.toFixed(2),4,yy+4)}const closes=bars.map(b=>b.c);for(const [n,color] of [[20,'#4ea1ff'],[50,'#ffcc66'],[200,'#d47cff']]){const avg=sma(closes,n);ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();avg.forEach((v,i)=>{if(v!==null){const xx=x(i),yy=y(v);i===0?ctx.moveTo(xx,yy):ctx.lineTo(xx,yy)}});ctx.stroke()}const range=hi-lo,levels=[.382,.5,.618];ctx.setLineDash([5,4]);levels.forEach(f=>{const yy=y(lo+range*f);ctx.strokeStyle='#39c5bb';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();ctx.fillStyle='#39c5bb';ctx.fillText((f*100).toFixed(1)+'%',left+5,yy-4)});ctx.setLineDash([]);bars.forEach((b,i)=>{const xx=x(i),bw=Math.max(2,pw/bars.length*.55);ctx.strokeStyle=b.c>=b.o?'#35c19f':'#ef6470';ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(xx,y(b.h));ctx.lineTo(xx,y(b.l));ctx.stroke();ctx.fillRect(xx-bw/2,y(Math.max(b.o,b.c)),bw,Math.max(1,Math.abs(y(b.o)-y(b.c))));});const last=bars[bars.length-1];document.getElementById('meta').innerHTML='<span>Last <b>'+last.c.toFixed(2)+'</b></span><span>O <b>'+last.o.toFixed(2)+'</b></span><span>H <b>'+last.h.toFixed(2)+'</b></span><span>L <b>'+last.l.toFixed(2)+'</b></span><span>C <b>'+last.c.toFixed(2)+'</b></span><span>Volume <b>'+last.v.toFixed(0)+'</b></span>'}
markets().then(()=>load('AAPL',['1m','5m','15m','30m','1d']));window.addEventListener('resize',draw);
</script></body></html>"""
                self._send(200, content.encode(), "text/html; charset=utf-8")
                return
            code, payload = api.get(parsed.path, parse_qs(parsed.query))
            self._send(code, payload)

        def do_POST(self) -> None:
            self._send(404, {"error": "write_control_disabled"})

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


class ReadAPIServer:
    def __init__(self, api: ReadAPI, host: str = "127.0.0.1", port: int = 8080) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("API host must be loopback-only")
        self.server = ThreadingHTTPServer((host, port), make_handler(api))

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
