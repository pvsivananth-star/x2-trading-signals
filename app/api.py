"""Stdlib, localhost-only, read-only HTTP API and dashboard."""

from __future__ import annotations

import json
import os
import ipaddress
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
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
                 store: object | None = None, actor: object | None = None,
                 broker: object | None = None) -> None:
        self.data = {key: tuple(values) for key, values in (data or {}).items()}
        self.token = token if token is not None else os.getenv("X2_API_TOKEN") or None
        self.store = store
        self.actor = actor
        self.broker = broker

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
                "/v1/portfolio", "/v1/position/{symbol}", "/v1/orders", "/v1/fills",
                "/v1/positions", "/v1/pnl", "/v1/backtests"
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
        if path == "/v1/portfolio":
            if not self.broker:
                return 200, {"kitty": "0", "open_position_value": "0", "overall_total": "0",
                              "unrealized_pnl": "0", "realized_pnl": "0", "positions": []}
            marks = self._position_marks()
            snapshot = self.broker.snapshot(datetime.now(UTC), marks)
            positions = []
            for position in self.broker.positions:
                mark = marks.get(position.symbol, position.average_cost)
                pnl = (mark - position.average_cost) * position.quantity
                pnl_pct = ((mark / position.average_cost) - 1) * 100 if position.average_cost else 0
                positions.append({"symbol": position.symbol, "quantity": str(position.quantity),
                                  "entry_price": str(position.average_cost), "current_value": str(mark * position.quantity),
                                  "average_cost": str(position.average_cost), "unrealized_pnl": str(pnl),
                                  "pnl_pct": str(pnl_pct)})
            return 200, {"kitty": str(snapshot.cash), "open_position_value": str(snapshot.positions_value),
                          "overall_total": str(snapshot.equity), "unrealized_pnl": str(snapshot.unrealized_pnl),
                          "realized_pnl": str(snapshot.realized_pnl), "positions": positions}
        if path.startswith("/v1/position/"):
            symbol = path.rsplit("/", 1)[-1]
            if not self.broker:
                return 200, {"symbol": symbol, "position": None, "fills": [],
                             "entry_time": None, "entry_price_executed": None, "expected_exit": None}
            position = next((p for p in self.broker.positions if p.symbol == symbol), None)
            if position is None:
                return 200, {"symbol": symbol, "position": None, "fills": [], "expected_exit": None}
            mark = self._position_marks().get(symbol, position.average_cost)
            fills = [asdict(fill) for fill in self.broker.fills if fill.symbol == symbol]
            first_buy = next((fill for fill in fills if fill["side"] == "buy"), None)
            target = self._expected_target(symbol)
            pnl = (mark - position.average_cost) * position.quantity
            pnl_pct = ((mark / position.average_cost) - 1) * 100 if position.average_cost else 0
            return 200, {"symbol": symbol,
                         "position": {"quantity": str(position.quantity), "entry_price": str(position.average_cost),
                                      "average_entry": str(position.average_cost), "current_mark": str(mark),
                                      "current_value": str(mark * position.quantity),
                                      "unrealized_pnl": str(pnl), "pnl_pct": str(pnl_pct)},
                         "entry_time": (first_buy["timestamp"].isoformat().replace("+00:00", "Z")
                                        if first_buy else None),
                         "entry_price_executed": str(first_buy["price"] if first_buy else position.average_cost),
                         "fills": utc_json(fills), "expected_exit": target}
        if path.startswith("/v1/broker"):
            # Read-only broker summaries are attached by callers through broker/audit repositories.
            repository = getattr(self.actor, "broker_audit", None) if self.actor else None
            if repository is not None:
                mapping = {
                    "/v1/broker/orders": repository.orders,
                    "/v1/broker/fills": repository.fills,
                    "/v1/broker/positions": repository.positions,
                    "/v1/broker/trades": repository.trades,
                    "/v1/broker/audit": repository.audit,
                }
                getter = mapping.get(path)
                if getter:
                    key = path.rsplit("/", 1)[-1]
                    return 200, {"items": utc_json(getter()), "total": len(getter())}
            if path == "/v1/broker/status":
                broker = getattr(self.actor, "mock_broker", None)
                return 200, {"mode": "demo", "live_write_enabled": False,
                             "connected": broker is not None,
                             "host": getattr(broker, "host", None), "port": getattr(broker, "port", None)}
            if path == "/v1/broker" or path == "/v1/broker/":
                return 200, {"status": "/v1/broker/status", "orders": "/v1/broker/orders",
                             "fills": "/v1/broker/fills", "positions": "/v1/broker/positions",
                             "trades": "/v1/broker/trades", "audit": "/v1/broker/audit"}
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

    def _position_marks(self) -> dict[str, object]:
        marks: dict[str, object] = {}
        if not self.store:
            return marks
        for position in getattr(self.broker, "positions", ()):
            candles = self.store.bars(position.symbol, "1m", 1)
            if candles:
                marks[position.symbol] = candles[-1].close
        return marks

    def _expected_target(self, symbol: str) -> object:
        if not self.actor:
            return None
        card = getattr(self.actor, "cards", {}).get(symbol)
        if card is None:
            return None
        inputs = getattr(card, "inputs", {})
        if getattr(card, "selected_fib_zone", None):
            target = inputs.get("fib_618")
            return None if target in {None, "N/A", "None"} else target
        return None

    def authorized(self, client_host: str, supplied_token: str | None) -> bool:
        container_mode = os.getenv("X2_CONTAINER_MODE") == "1"
        if container_mode:
            try:
                address = ipaddress.ip_address(client_host)
            except ValueError:
                return False
            if not address.is_private and not address.is_loopback:
                return False
        elif client_host not in {"127.0.0.1", "::1", "localhost"}:
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
<style>body{margin:0;font-family:system-ui;background:#0b1020;color:#dfe7ff}aside{position:fixed;width:220px;height:100%;overflow:auto;background:#11172a;border-right:1px solid #26304a;padding:16px}.market{margin:14px 0 6px;color:#91a4cc;font-size:.8rem;text-transform:uppercase}.symbol{display:block;width:100%;border:0;border-radius:6px;margin:3px 0;padding:8px;text-align:left;background:transparent;color:#dfe7ff;cursor:pointer}.symbol.active{background:#263d72}main{margin-left:252px;padding:16px}.portfolio{position:fixed;z-index:5;top:0;left:252px;right:0;height:68px;display:flex;gap:28px;align-items:center;padding:0 16px;background:#11172a;border-bottom:1px solid #26304a}.portfolio div{min-width:145px}.portfolio label{display:block;color:#91a4cc;font-size:.72rem;text-transform:uppercase}.tabs{margin-top:68px}.tabs button{margin-right:4px}.tab-content{margin:12px 0}.positions-only{display:none}canvas{width:100%;height:560px;background:#090d18;border:1px solid #26304a}.meta{display:flex;gap:18px;color:#aab7d2}.controls{margin:10px 0}.controls button{margin-right:4px}.badge{display:inline-block;margin-right:8px;border-radius:4px;padding:4px 8px;font-weight:700}.buy{background:#166b54;color:#baffea}.sell{background:#82343d;color:#ffd1d5}.hold{background:#39445e;color:#dfe7ff}.reason{margin:10px 0;color:#dfe7ff}.params{color:#91a4cc;font-size:.85rem}.synthetic{color:#ff9f43}</style></head>
<body><aside><h2>X2 Markets</h2><div class='tabs'><button onclick="setTab('positions')">Positions</button><button onclick="setTab('others')">Others</button></div><div id='markets'></div></aside><div class='portfolio'><div><label>Kitty</label><b id='kitty'>—</b></div><div><label>Open Position</label><b id='open-value'>—</b></div><div><label>Overall Total</label><b id='overall'>—</b></div><div><label>Unrealized P&amp;L</label><b id='unrealized'>—</b></div></div><main><h1 id='title'>Select a symbol</h1>
<div class='controls'><label>Timeframe </label><span id='tfs'></span></div><div id='analysis'></div><div class='meta' id='meta'></div>
<canvas id='chart'></canvas><div id='notice'></div></main><script>
let active=null,interval='1m',tab='positions',allSymbols={},positionSymbols=[];
async function refreshPortfolio(){const d=await(await fetch('/v1/portfolio')).json();document.getElementById('kitty').textContent=d.kitty;document.getElementById('open-value').textContent=d.open_position_value;document.getElementById('overall').textContent=d.overall_total;document.getElementById('unrealized').textContent=d.unrealized_pnl;positionSymbols=(d.positions||[]).map(p=>p.symbol)}
function setTab(next){tab=next;renderMarkets()}
function renderMarkets(){const box=document.getElementById('markets');box.innerHTML='';for(const [market,items] of Object.entries(allSymbols)){const shown=items.filter(s=>tab==='positions'?positionSymbols.includes(s.symbol):!positionSymbols.includes(s.symbol));if(!shown.length)continue;const h=document.createElement('div');h.className='market';h.textContent=market+(tab==='positions'?' · Positions':' · Others');box.append(h);for(const s of shown){const b=document.createElement('button');b.className='symbol';b.textContent=s.symbol;b.onclick=()=>load(s.symbol,s.timeframes||[]);box.append(b)}}}
async function markets(){await refreshPortfolio();const r=await fetch('/v1/markets');const d=await r.json();allSymbols={};for(const m of d.items||[]){allSymbols[m.market]=(allSymbols[m.market]||[]).concat(m.symbols)}renderMarkets();const first=tab==='positions'?positionSymbols[0]:Object.values(allSymbols).flat()[0]?.symbol||'AAPL';if(first){const s=Object.values(allSymbols).flat().find(v=>v.symbol===first);load(first,s?.timeframes||['1m','5m','15m','30m','1d'])}}
function sma(values,n){return values.map((_,i)=>i<n-1?null:values.slice(i-n+1,i+1).reduce((a,b)=>a+b,0)/n)}
async function load(symbol,timeframes){active=symbol;if(!timeframes.includes(interval))interval=timeframes.includes('1m')?'1m':timeframes[0];document.querySelectorAll('.symbol').forEach(b=>b.classList.toggle('active',b.textContent===symbol));const spans=document.getElementById('tfs');spans.innerHTML='';for(const tf of timeframes||['1m','5m','15m','30m','1d']){const b=document.createElement('button');b.textContent=tf;b.onclick=()=>{interval=tf;load(symbol,timeframes)};spans.append(b)}await draw()}
async function draw(){if(!active)return;await refreshPortfolio();const r=await fetch('/v1/bars?symbol='+encodeURIComponent(active)+'&interval='+interval+'&limit=300');const d=await r.json();const bars=(d.items||[]).map(v=>({t:new Date(v.bar_start),o:+v.open,h:+v.high,l:+v.low,c:+v.close,v:+v.volume}));const pd=await(await fetch('/v1/position/'+encodeURIComponent(active))).json();document.getElementById('title').textContent=active+' · '+interval;document.getElementById('notice').innerHTML=d.synthetic?'<span class="synthetic">SYNTHETIC TEST FIXTURE — not live market data</span>':'';const sr=await fetch('/v1/signals');const sd=await sr.json();const card=(sd.items||[]).find(v=>v.symbol===active);if(card){const action=(card.display_action||'HOLD').toLowerCase();document.getElementById('analysis').innerHTML='<span class="badge '+action+'">'+(card.display_action||'HOLD')+'</span><div class="reason">'+(card.reason||'No rule explanation')+'</div><div class="params">'+Object.entries(card.parameters||{}).map(([k,v])=>k+'='+v).join(' · ')+'</div>'}else{document.getElementById('analysis').innerHTML=''}if(!bars.length){document.getElementById('meta').textContent='No bars';return}drawChart(bars,pd)}
function drawChart(bars,position){const canvas=document.getElementById('chart'),ctx=canvas.getContext('2d'),dpr=window.devicePixelRatio||1,rect=canvas.getBoundingClientRect();canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;ctx.scale(dpr,dpr);const w=rect.width,h=rect.height,left=45,right=20,top=20,bottom=30,pw=w-left-right,ph=h-top-bottom;ctx.clearRect(0,0,w,h);ctx.fillStyle='#090d18';ctx.fillRect(0,0,w,h);const lows=bars.map(b=>b.l),highs=bars.map(b=>b.h),min=Math.min(...lows),max=Math.max(...highs),pad=(max-min)*.08||1,lo=min-pad,hi=max+pad;const x=i=>left+i*pw/(bars.length-1||1),y=v=>top+(hi-v)*ph/(hi-lo);ctx.strokeStyle='#26304a';ctx.lineWidth=1;ctx.font='11px system-ui';ctx.fillStyle='#aab7d2';for(let i=0;i<5;i++){const yy=top+i*ph/4,val=hi-i*(hi-lo)/4;ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();ctx.fillText(val.toFixed(2),4,yy+4)}const closes=bars.map(b=>b.c);for(const [n,color] of [[20,'#4ea1ff'],[50,'#ffcc66'],[200,'#d47cff']]){const avg=sma(closes,n);ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();avg.forEach((v,i)=>{if(v!==null){const xx=x(i),yy=y(v);i===0?ctx.moveTo(xx,yy):ctx.lineTo(xx,yy)}});ctx.stroke()}const range=hi-lo,levels=[.382,.5,.618];ctx.setLineDash([5,4]);levels.forEach(f=>{const yy=y(lo+range*f);ctx.strokeStyle='#39c5bb';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();ctx.fillStyle='#39c5bb';ctx.fillText((f*100).toFixed(1)+'%',left+5,yy-4)});ctx.setLineDash([]);bars.forEach((b,i)=>{const xx=x(i),bw=Math.max(2,pw/bars.length*.55);ctx.strokeStyle=b.c>=b.o?'#35c19f':'#ef6470';ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(xx,y(b.h));ctx.lineTo(xx,y(b.l));ctx.stroke();ctx.fillRect(xx-bw/2,y(Math.max(b.o,b.c)),bw,Math.max(1,Math.abs(y(b.o)-y(b.c))));});if(position&&position.entry_time){const entry=position.entry_time?new Date(position.entry_time).getTime():null;let entryIndex=-1;if(entry!==null){entryIndex=bars.findIndex(b=>b.t.getTime()===entry);if(entryIndex<0){entryIndex=bars.reduce((best,b,i)=>Math.abs(b.t.getTime()-entry)<Math.abs(bars[best].t.getTime()-entry)?i:best,0)}}const xx=x(entryIndex);ctx.setLineDash([6,3]);ctx.strokeStyle='#ff9f43';ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,h-bottom);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#ff9f43';ctx.fillText('Executed '+position.entry_price_executed,xx+5,top+12)}if(position&&position.expected_exit!=null){const target=+position.expected_exit;if(target>=lo&&target<=hi){const yy=y(target);ctx.setLineDash([6,3]);ctx.strokeStyle='#7cf29c';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#7cf29c';ctx.fillText('Expected exit '+target.toFixed(2),w-right-130,yy-5)}}const last=bars[bars.length-1];document.getElementById('meta').innerHTML='<span>Last <b>'+last.c.toFixed(2)+'</b></span><span>O <b>'+last.o.toFixed(2)+'</b></span><span>H <b>'+last.h.toFixed(2)+'</b></span><span>L <b>'+last.l.toFixed(2)+'</b></span><span>C <b>'+last.c.toFixed(2)+'</b></span><span>Volume <b>'+last.v.toFixed(0)+'</b></span>'}
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
        if host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
            raise ValueError("API host must be loopback-only or container wildcard")
        if host == "0.0.0.0" and os.getenv("X2_CONTAINER_MODE") != "1":
            raise ValueError("Container wildcard host requires X2_CONTAINER_MODE=1")
        self.server = ThreadingHTTPServer((host, port), make_handler(api))

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
