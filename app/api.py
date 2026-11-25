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
    def __init__(self, data: Mapping[str, Iterable[object]] | None = None, token: str | None = None) -> None:
        self.data = {key: tuple(values) for key, values in (data or {}).items()}
        self.token = token if token is not None else os.getenv("X2_API_TOKEN") or None

    def get(self, path: str, query: Mapping[str, list[str]] | None = None) -> tuple[int, object]:
        if path in {"/", "/dashboard", "/dashboard/"}:
            return 200, {"dashboard": "/v1/dashboard"}
        if path in {"/health", "/v1/health"}:
            return 200, {"status": "ok", "live_write_enabled": False}
        if path == "/v1/dashboard":
            return 200, {"template": "localhost_read_only_dashboard", "endpoints": [
                "/v1/health", "/v1/ingestion", "/v1/quality", "/v1/bars", "/v1/signals",
                "/v1/orders", "/v1/fills", "/v1/positions", "/v1/pnl", "/v1/backtests"
            ]}
        contract = {
            "/v1/ingestion": "ingestion", "/v1/quality": "quality", "/v1/bars": "bars",
            "/v1/signals": "signals", "/v1/orders": "orders", "/v1/fills": "fills",
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
                content = """<!doctype html><html><meta charset='utf-8'><title>X2 Read Dashboard</title>
                <style>body{font-family:sans-serif;margin:2rem}pre{background:#eee;padding:1rem}</style>
                <h1>X2 Read-only Demo Dashboard</h1><p id='health'></p>
                <div id='summary'></div><script>
                const endpoints=['health','ingestion','quality','bars','signals','orders','fills','positions','pnl','backtests'];
                Promise.all(endpoints.map(e=>fetch('/v1/'+e).then(r=>r.json()).then(v=>[e,v])))
                .then(rows=>document.getElementById('summary').innerHTML='<pre>'+rows.map(r=>r[0]+': '+JSON.stringify(r[1])).join('\n')+'</pre>');
                fetch('/v1/health').then(r=>r.json()).then(v=>document.getElementById('health').textContent=JSON.stringify(v));
                </script></html>"""
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
