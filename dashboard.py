"""UAA Dashboard — monitor and manage MCP wrappers."""
from __future__ import annotations
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PYTHON = sys.executable

_IDLE_TIMEOUT    = 30 * 60       # seconds of inactivity before shutdown
_ABSOLUTE_LIMIT  = 2  * 60 * 60  # maximum lifetime regardless of activity
_CLEANUP_INTERVAL = 5 * 60       # how often the cleanup task runs

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

BASE = Path(__file__).parent
SCHEMAS_DIR = BASE / "schemas"

_logo_path = BASE / "mi4.png"
_LOGO_DATA = (
    "data:image/png;base64," + base64.b64encode(_logo_path.read_bytes()).decode()
    if _logo_path.exists() else ""
)


_ALLOWLIST = set(filter(None, os.environ.get("RATE_LIMIT_ALLOWLIST", "").split(",")))

def _get_ip(request: Request) -> str:
    ip = get_remote_address(request)
    return "allowlisted" if ip in _ALLOWLIST else ip


@asynccontextmanager
async def lifespan(app: FastAPI):
    _restore_state()
    task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


limiter = Limiter(key_func=_get_ip)
app = FastAPI(title="UAA Dashboard", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# stem -> live ASGI MCP app (in-process, no subprocesses)
_mcp_apps: dict[str, Any] = {}
# stem -> {started_at: float, last_used: float}  (monotonic clock)
_server_meta: dict[str, dict] = {}

async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL)
        now = time.monotonic()
        to_remove = [
            stem for stem, meta in list(_server_meta.items())
            if now - meta["last_used"] > _IDLE_TIMEOUT
            or now - meta["started_at"] > _ABSOLUTE_LIMIT
        ]
        for stem in to_remove:
            _mcp_apps.pop(stem, None)
            _server_meta.pop(stem, None)
        if to_remove:
            _save_state()


def _make_mcp_asgi(stem: str, schema_data: dict) -> Any:
    from src.discovery.schema import CondensedSchema
    from src.serving.mcp_server import create_mcp_app
    schema = CondensedSchema(**schema_data)
    return create_mcp_app(
        schema,
        sse_path=f"/mcp/{stem}",
        messages_path=f"/mcp/{stem}/messages/",
    )


def _save_state() -> None:
    pass  # no-op: state is derived from schemas/ on startup


def _restore_state() -> None:
    if not SCHEMAS_DIR.exists():
        return
    now = time.monotonic()
    for schema_path in sorted(SCHEMAS_DIR.glob("*.json")):
        if schema_path.name.startswith("."):
            continue
        stem = schema_path.stem
        try:
            schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
            _mcp_apps[stem] = _make_mcp_asgi(stem, schema_data)
            _server_meta[stem] = {"started_at": now, "last_used": now}
        except Exception:
            pass


class _MCPDispatch:
    """ASGI middleware that routes /mcp/{stem} requests to in-process MCP apps."""
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("lifespan", "websocket"):
            await self._inner(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/mcp/"):
            stem = path[5:].split("/")[0]
            mcp_app = _mcp_apps.get(stem)
            if mcp_app:
                if stem in _server_meta:
                    _server_meta[stem]["last_used"] = time.monotonic()
                await mcp_app(scope, receive, send)
                return
        await self._inner(scope, receive, send)


# Wrap the FastAPI app so /mcp/{stem} requests are dispatched in-process.
# Dockerfile CMD and __main__ both point uvicorn at this object.
wrapped_app = _MCPDispatch(app)


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_HTML.replace("__MI_LOGO__", _LOGO_DATA))


@app.get("/api/schemas")
def list_schemas():
    out = []
    for f in sorted(f for f in SCHEMAS_DIR.glob("*.json") if not f.name.startswith(".")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "file": f.name,
                "service_name": d.get("service_name", f.stem),
                "description": d.get("service_description", ""),
                "tool_count": len(d.get("tools", [])),
                "tools": [t["name"] for t in d.get("tools", [])],
                "auth_type": d.get("auth_type", "none"),
                "created_at": d.get("created_at", ""),
                "source_url": d.get("source_url", ""),
                "discovery_method": d.get("discovery_method", "openapi"),
            })
        except Exception:
            pass
    return out


@app.get("/api/servers")
def list_servers():
    return {stem: {"schema": f"{stem}.json", "mcp_path": f"/mcp/{stem}"}
            for stem in _mcp_apps}


class StartReq(BaseModel):
    schema_file: str


@app.post("/api/servers")
def start_server(req: StartReq):
    schema_path = (SCHEMAS_DIR / req.schema_file).resolve()
    if not schema_path.is_relative_to(SCHEMAS_DIR.resolve()):
        raise HTTPException(400, "Invalid schema path")
    if not schema_path.exists():
        raise HTTPException(404, "Schema not found")
    stem = Path(req.schema_file).stem
    if stem not in _mcp_apps:
        schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
        _mcp_apps[stem] = _make_mcp_asgi(stem, schema_data)
        now = time.monotonic()
        _server_meta[stem] = {"started_at": now, "last_used": now}
        _save_state()
    return {"stem": stem, "mcp_path": f"/mcp/{stem}"}


@app.delete("/api/servers/{stem}")
def stop_server(stem: str):
    if stem not in _mcp_apps:
        raise HTTPException(404, "No server for that schema")
    del _mcp_apps[stem]
    _server_meta.pop(stem, None)
    _save_state()
    return {"stopped": stem}


class DiscoverReq(BaseModel):
    url: str
    spec_url: str = ""
    tags: str = ""


@app.post("/api/discover/stream")
@limiter.limit("5/day")
async def discover_stream(request: Request, req: DiscoverReq):
    async def generate():
        args = [PYTHON, str(BASE / "discover.py"), "--url", req.url]
        if req.spec_url:
            args += ["--spec", req.spec_url]
        if req.tags:
            args += ["--tags", req.tags]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async with asyncio.timeout(120):
                async for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    yield f"data: {json.dumps({'line': line})}\n\n"
        except TimeoutError:
            proc.kill()
            yield f"data: {json.dumps({'line': '[FAIL] Discovery timed out after 120s'})}\n\n"
        finally:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                pass
        yield f"data: {json.dumps({'done': True, 'exit_code': proc.returncode if proc.returncode is not None else 1})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── HTML ─────────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Machine Internet UAA</title>
<link rel="icon" type="image/png" href="__MI_LOGO__">
<style>
/* ── tokens ───────────────────────────────────────────────────────────────── */
html[data-theme="dark"]{
  --bg:#09090b;--surface:#111113;--surface2:#1c1c1f;
  --border:#2e2e33;--text:#f4f4f5;--muted:#71717a;
  --sh:0 1px 3px rgba(0,0,0,.5),0 8px 24px rgba(0,0,0,.35);
  --sh-sm:0 1px 2px rgba(0,0,0,.4);
}
html[data-theme="light"]{
  --bg:#f9f9fb;--surface:#ffffff;--surface2:#f4f4f6;
  --border:#e4e4e8;--text:#111113;--muted:#71717a;
  --sh:0 1px 3px rgba(0,0,0,.06),0 8px 24px rgba(0,0,0,.05);
  --sh-sm:0 1px 2px rgba(0,0,0,.05);
}
:root{
  --accent:#7c3aed;--accent-h:#6d28d9;--accent-s:#ede9fe;
  --green:#16a34a;--green-bg:#f0fdf4;--green-border:#bbf7d0;
  --red:#dc2626;--yellow:#d97706;--blue:#2563eb;
  --mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
  --r:8px;--r-sm:6px;--t:150ms ease;
}
html[data-theme="dark"]{
  --green-bg:#052e16;--green-border:#166534;
  --accent-s:#2e1065;
}

/* ── reset ────────────────────────────────────────────────────────────────── */
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100vh;overflow:hidden}
body{background:var(--bg);color:var(--text);
  font-family:ui-sans-serif,-apple-system,'Segoe UI',system-ui,sans-serif;
  font-size:14px;line-height:1.5;display:flex;flex-direction:column}
button{font-family:inherit;cursor:pointer}
input{font-family:inherit}

/* ── header ───────────────────────────────────────────────────────────────── */
header{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 1.25rem;height:52px;display:flex;align-items:center;
  justify-content:space-between;flex-shrink:0;gap:1rem;
}
.logo{display:flex;align-items:center;gap:.6rem;flex-shrink:0}
.logo-mark{
  width:28px;height:28px;border-radius:7px;flex-shrink:0;
  object-fit:contain;display:block;
}
.logo-name{font-size:.875rem;font-weight:600;letter-spacing:-.02em}
.logo-name span{color:var(--muted);font-weight:400}
.hdr-right{display:flex;align-items:center;gap:.5rem}
.count-pill{
  background:var(--surface2);border:1px solid var(--border);
  border-radius:999px;padding:.2rem .65rem;
  font-size:.72rem;font-weight:500;color:var(--muted);
}
.count-pill b{color:var(--text);font-weight:600}
.icon-btn{
  width:32px;height:32px;display:flex;align-items:center;justify-content:center;
  background:var(--surface2);border:1px solid var(--border);border-radius:var(--r-sm);
  color:var(--muted);font-size:.9rem;transition:all var(--t);
}
.icon-btn:hover{background:var(--border);color:var(--text)}

/* ── wrap section ─────────────────────────────────────────────────────────── */
.wrap-section{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:.875rem 1.25rem;flex-shrink:0;
}
.wrap-row{display:flex;gap:.5rem;align-items:stretch}
.wrap-input{
  flex:1;min-width:0;
  background:var(--bg);border:1.5px solid var(--border);border-radius:var(--r);
  padding:.6rem .875rem;color:var(--text);font-size:.875rem;
  font-family:var(--mono);outline:none;
  transition:border-color var(--t),box-shadow var(--t);
}
.wrap-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(124,58,237,.12)}
.wrap-input::placeholder{color:var(--muted);font-size:.8rem}
.btn-primary{
  background:var(--accent);color:#fff;border:none;border-radius:var(--r);
  padding:.6rem 1.1rem;font-size:.875rem;font-weight:600;white-space:nowrap;
  transition:background var(--t),transform var(--t),box-shadow var(--t);
}
.btn-primary:hover{background:var(--accent-h);box-shadow:0 2px 8px rgba(124,58,237,.35)}
.btn-primary:active{transform:translateY(1px)}
.btn-primary:disabled{opacity:.55;cursor:not-allowed;transform:none;box-shadow:none}
.btn-ghost{
  background:none;border:1.5px solid var(--border);border-radius:var(--r);
  padding:.6rem .75rem;font-size:.8rem;color:var(--muted);white-space:nowrap;
  transition:all var(--t);
}
.btn-ghost:hover{border-color:var(--muted);color:var(--text)}
.opts-panel{
  display:none;margin-top:.75rem;padding-top:.75rem;
  border-top:1px solid var(--border);gap:.75rem;
}
.opts-panel.open{display:flex}
.opt-field{flex:1}
.opt-label{
  display:block;font-size:.7rem;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:.3rem;
}
.opt-input{
  width:100%;background:var(--bg);border:1.5px solid var(--border);border-radius:var(--r-sm);
  padding:.5rem .7rem;color:var(--text);font-size:.8rem;font-family:var(--mono);outline:none;
  transition:border-color var(--t);
}
.opt-input:focus{border-color:var(--accent)}

/* ── body layout ──────────────────────────────────────────────────────────── */
.body-area{display:flex;flex:1;min-height:0;overflow:hidden}

/* ── grid ─────────────────────────────────────────────────────────────────── */
.grid-area{flex:1;min-width:0;overflow-y:auto;padding:1.25rem}
.grid-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;gap:.75rem}
.section-label{font-size:.7rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.svc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:.875rem}
.search-wrap{position:relative;display:flex;align-items:center}
.search-input{
  background:var(--surface2);border:1px solid var(--border);border-radius:var(--r);
  color:var(--text);font-size:.8rem;padding:.35rem .65rem .35rem 1.75rem;width:180px;
  transition:border-color var(--t),box-shadow var(--t),width var(--t);outline:none;
}
.search-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(124,58,237,.12);width:220px}
.search-input::placeholder{color:var(--muted)}
.search-icon{position:absolute;left:.5rem;font-size:.75rem;color:var(--muted);pointer-events:none}
.search-clear{position:absolute;right:.4rem;font-size:.7rem;color:var(--muted);cursor:pointer;
  background:none;border:none;padding:0 .2rem;display:none}
.search-clear.visible{display:block}
.search-clear:hover{color:var(--text)}

/* ── service card ─────────────────────────────────────────────────────────── */
.svc-card{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r);padding:1rem;cursor:pointer;
  display:flex;flex-direction:column;gap:.75rem;
  transition:box-shadow var(--t),transform var(--t),border-color var(--t);
  position:relative;overflow:hidden;
}
.svc-card::before{
  content:'';position:absolute;top:0;left:0;bottom:0;width:3px;
  background:transparent;transition:background var(--t);
}
.svc-card.live::before{background:var(--green)}
.svc-card:hover{box-shadow:var(--sh);transform:translateY(-1px)}
.svc-card.selected{border-color:var(--accent);box-shadow:0 0 0 3px rgba(124,58,237,.15)}
.svc-card.selected::before{background:var(--accent)}

.card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:.5rem}
.svc-name{font-size:.9rem;font-weight:600;letter-spacing:-.01em;line-height:1.3}
.svc-url{font-size:.7rem;color:var(--muted);font-family:var(--mono);margin-top:.2rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.pills{display:flex;flex-wrap:wrap;gap:.3rem}
.pill{
  display:inline-flex;align-items:center;gap:.25rem;
  padding:.2rem .5rem;border-radius:999px;font-size:.7rem;font-weight:500;
  white-space:nowrap;border:1px solid transparent;
}
html[data-theme="dark"] .p-a{background:#1e2d4a;color:#93c5fd;border-color:#1d3a6b}
html[data-theme="light"] .p-a{background:#eff6ff;color:#1d4ed8;border-color:#bfdbfe}
html[data-theme="dark"] .p-b{background:#2d200e;color:#fcd34d;border-color:#4a3010}
html[data-theme="light"] .p-b{background:#fffbeb;color:#92400e;border-color:#fde68a}
.p-tools{background:var(--surface2);color:var(--muted);border-color:var(--border)}
html[data-theme="dark"] .p-live{background:var(--green-bg);color:#4ade80;border-color:var(--green-border)}
html[data-theme="light"] .p-live{background:var(--green-bg);color:#15803d;border-color:var(--green-border)}
.p-stopped{background:var(--surface2);color:var(--muted);border-color:var(--border)}

.live-dot{width:6px;height:6px;border-radius:50%;background:currentColor;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.p-live .live-dot{animation:pulse 2s ease-in-out infinite}

.mcp-row{
  display:flex;align-items:center;gap:.4rem;
  background:var(--surface2);border:1px solid var(--border);
  border-radius:var(--r-sm);padding:.3rem .6rem;
}
.mcp-url{font-size:.7rem;font-family:var(--mono);color:var(--muted);flex:1;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-copy-sm{
  background:none;border:none;color:var(--muted);font-size:.75rem;
  padding:.1rem .25rem;border-radius:4px;flex-shrink:0;
  transition:color var(--t),background var(--t);
}
.btn-copy-sm:hover{color:var(--text);background:var(--border)}

.card-actions{display:flex;gap:.4rem}
.btn-sm{
  padding:.35rem .75rem;font-size:.75rem;font-weight:500;border-radius:var(--r-sm);
  border:1px solid transparent;transition:all var(--t);
}
html[data-theme="dark"] .btn-start{background:#052e16;color:#4ade80;border-color:#166534}
html[data-theme="light"] .btn-start{background:#f0fdf4;color:#15803d;border-color:#86efac}
.btn-start:hover{filter:brightness(1.15)}
html[data-theme="dark"] .btn-stop{background:#450a0a;color:#f87171;border-color:#7f1d1d}
html[data-theme="light"] .btn-stop{background:#fef2f2;color:#dc2626;border-color:#fca5a5}
.btn-stop:hover{filter:brightness(1.1)}

.grid-empty{
  grid-column:1/-1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;padding:4rem 2rem;gap:.75rem;
  color:var(--muted);text-align:center;
}
.grid-empty-icon{font-size:2rem;opacity:.3}
.grid-empty-text{font-size:.875rem}
.grid-empty-hint{font-size:.78rem;opacity:.7}

/* ── sidebar ──────────────────────────────────────────────────────────────── */
.sidebar{
  width:288px;flex-shrink:0;
  background:var(--surface);border-left:1px solid var(--border);
  display:flex;flex-direction:column;
}
.sb-tabs{display:flex;border-bottom:1px solid var(--border);flex-shrink:0;padding:0 .5rem}
.tab-btn{
  flex:1;background:none;border:none;border-bottom:2px solid transparent;
  padding:.7rem .5rem;font-size:.78rem;font-weight:500;color:var(--muted);
  transition:all var(--t);
}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.tab-panel{flex:1;overflow-y:auto;display:none;flex-direction:column}
.tab-panel.active{display:flex}

.log-area{
  flex:1;padding:.75rem;font-family:var(--mono);font-size:.72rem;
  line-height:1.8;overflow-y:auto;
}
.log-hint{
  color:var(--muted);text-align:center;padding:2.5rem 1.5rem;
  font-size:.8rem;font-family:inherit;line-height:1.6;
}
.ll{color:var(--text)}.lk{color:var(--green)}.lf{color:var(--red)}.ly{color:var(--yellow)}

.evals-area{padding:.875rem;display:flex;flex-direction:column;gap:1rem}
.eval-row{}
.eval-hdr{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:.35rem}
.eval-name{font-size:.78rem;font-weight:600}
.eval-score{font-size:.72rem;font-weight:500;color:var(--muted)}
.eval-score.pass{color:var(--green)}.eval-score.fail{color:var(--red)}
.eval-track{height:4px;background:var(--surface2);border-radius:999px;overflow:hidden}
.eval-bar{height:100%;border-radius:999px;background:var(--green);transition:width .6s ease}
.eval-bar.fail{background:var(--red)}
.eval-meta{font-size:.68rem;color:var(--muted);margin-top:.25rem}

.auth-area{padding:.875rem;display:flex;flex-direction:column;gap:.875rem}
.auth-hint{color:var(--muted);font-size:.8rem;text-align:center;padding:2rem 1rem;line-height:1.6}
.auth-group{display:flex;flex-direction:column;gap:.2rem}
.auth-label{font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.auth-value{font-size:.8rem;color:var(--text)}
.auth-cmd{
  background:var(--bg);border:1px solid var(--border);border-radius:var(--r-sm);
  padding:.5rem .65rem;font-family:var(--mono);font-size:.68rem;
  color:var(--muted);word-break:break-all;line-height:1.6;
}

/* ── metrics bar ──────────────────────────────────────────────────────────── */
.metrics-bar{
  background:var(--surface);border-top:1px solid var(--border);
  padding:.625rem 1.25rem;display:flex;gap:2rem;flex-shrink:0;
}
.metric{display:flex;align-items:baseline;gap:.4rem}
.m-num{font-size:1.1rem;font-weight:700;letter-spacing:-.02em;color:var(--accent)}
.m-lbl{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}

/* ── footer ───────────────────────────────────────────────────────────────── */
footer{
  background:var(--surface);border-top:1px solid var(--border);
  padding:.75rem 1.25rem;display:flex;align-items:center;gap:.75rem;flex-shrink:0;
}
.ft-label{font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);white-space:nowrap}
.ft-url{
  flex:1;min-width:0;font-family:var(--mono);font-size:.8rem;color:var(--text);
  padding:.4rem .65rem;background:var(--bg);border:1px solid var(--border);
  border-radius:var(--r-sm);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  transition:border-color var(--t);
}
.ft-url.empty{color:var(--muted)}
.btn-copy-ft{
  background:var(--accent);color:#fff;border:none;border-radius:var(--r-sm);
  padding:.45rem 1rem;font-size:.8rem;font-weight:600;white-space:nowrap;
  transition:background var(--t),box-shadow var(--t);
}
.btn-copy-ft:hover{background:var(--accent-h);box-shadow:0 2px 8px rgba(124,58,237,.35)}
.btn-copy-ft:disabled{opacity:.45;cursor:default;box-shadow:none}

/* ── mobile ───────────────────────────────────────────────────────────────── */
@media(max-width:860px){
  html,body{height:auto;overflow:auto}
  .body-area{flex-direction:column;overflow:visible}
  .grid-area{overflow:visible;padding:1rem}
  .sidebar{width:100%;border-left:none;border-top:1px solid var(--border);max-height:360px}
  .svc-grid{grid-template-columns:1fr}
  .svc-card:hover{transform:none}
  .logo-name span{display:none}
  .opts-panel.open{flex-direction:column}
  .metrics-bar{gap:1.25rem}
  footer{flex-wrap:wrap}
  .ft-url{min-width:100%;order:3}
}
@media(max-width:480px){
  header{padding:0 1rem}
  .wrap-section{padding:.75rem 1rem}
  .grid-area{padding:.875rem 1rem}
  .wrap-row{flex-wrap:wrap}
  .btn-primary{flex:1}
  .btn-ghost{flex:1}
  .metrics-bar{padding:.5rem 1rem;gap:1rem}
  footer{padding:.65rem 1rem}
}

/* ── wiki modal ───────────────────────────────────────────────────────────── */
.wiki-backdrop{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);
  backdrop-filter:blur(4px);z-index:100;align-items:center;justify-content:center;
  padding:1.5rem;
}
.wiki-backdrop.open{display:flex}
.wiki-modal{
  background:var(--surface);border:1px solid var(--border);border-radius:12px;
  box-shadow:0 8px 48px rgba(0,0,0,.5);max-width:680px;width:100%;
  max-height:85vh;display:flex;flex-direction:column;overflow:hidden;
}
.wiki-hdr{
  display:flex;align-items:center;justify-content:space-between;
  padding:1rem 1.25rem;border-bottom:1px solid var(--border);flex-shrink:0;
}
.wiki-title{font-size:.95rem;font-weight:700;letter-spacing:-.015em}
.wiki-close{
  width:28px;height:28px;display:flex;align-items:center;justify-content:center;
  background:none;border:1px solid var(--border);border-radius:6px;
  color:var(--muted);font-size:1rem;transition:all var(--t);
}
.wiki-close:hover{background:var(--border);color:var(--text)}
.wiki-body{flex:1;overflow-y:auto;padding:1.25rem;display:flex;flex-direction:column;gap:1.5rem}
.wiki-section{}
.wiki-section h3{
  font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;
  color:var(--accent);margin-bottom:.75rem;padding-bottom:.4rem;
  border-bottom:1px solid var(--border);
}
.wiki-section p{font-size:.83rem;line-height:1.7;color:var(--text);margin-bottom:.6rem}
.wiki-section p:last-child{margin-bottom:0}
.wiki-section strong{color:var(--text);font-weight:600}
.wiki-steps{display:flex;flex-direction:column;gap:.5rem;margin-top:.25rem}
.wiki-step{
  display:flex;align-items:flex-start;gap:.75rem;
  background:var(--surface2);border:1px solid var(--border);border-radius:var(--r);
  padding:.65rem .875rem;
}
.step-num{
  width:20px;height:20px;border-radius:50%;background:var(--accent);color:#fff;
  font-size:.68rem;font-weight:700;display:flex;align-items:center;justify-content:center;
  flex-shrink:0;margin-top:.05rem;
}
.step-body{font-size:.8rem;line-height:1.6}
.step-body b{color:var(--text)}
.wiki-path{
  display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-top:.25rem;
}
@media(max-width:480px){.wiki-path{grid-template-columns:1fr}}
.wiki-path-card{
  background:var(--surface2);border:1px solid var(--border);border-radius:var(--r);
  padding:.75rem;
}
.wpc-badge{
  display:inline-flex;padding:.15rem .55rem;border-radius:999px;
  font-size:.68rem;font-weight:700;margin-bottom:.5rem;
}
.wpc-a{background:#1e2d4a;color:#93c5fd}
html[data-theme="light"] .wpc-a{background:#eff6ff;color:#1d4ed8}
.wpc-b{background:#2d200e;color:#fcd34d}
html[data-theme="light"] .wpc-b{background:#fffbeb;color:#92400e}
.wpc-title{font-size:.8rem;font-weight:600;margin-bottom:.3rem}
.wpc-desc{font-size:.75rem;color:var(--muted);line-height:1.6}
.wiki-code{
  background:var(--bg);border:1px solid var(--border);border-radius:var(--r-sm);
  padding:.5rem .75rem;font-family:var(--mono);font-size:.72rem;color:var(--muted);
  line-height:1.7;white-space:pre-wrap;margin-top:.4rem;
}
.wiki-errs{display:flex;flex-direction:column;gap:.5rem;margin-top:.25rem}
.wiki-err{
  background:var(--surface2);border:1px solid var(--border);border-radius:var(--r);
  padding:.65rem .875rem;
}
.werr-hdr{display:flex;align-items:center;gap:.5rem;margin-bottom:.3rem}
.werr-tag{
  font-size:.6rem;font-weight:700;letter-spacing:.06em;padding:.1rem .4rem;
  border-radius:4px;flex-shrink:0;
}
.werr-tag.fail{background:#450a0a;color:#f87171}
html[data-theme="light"] .werr-tag.fail{background:#fef2f2;color:#dc2626}
.werr-tag.warn{background:#2d200e;color:#fcd34d}
html[data-theme="light"] .werr-tag.warn{background:#fffbeb;color:#92400e}
.werr-tag.info{background:#1e2d4a;color:#93c5fd}
html[data-theme="light"] .werr-tag.info{background:#eff6ff;color:#1d4ed8}
.werr-title{font-size:.8rem;font-weight:600;color:var(--text)}
.werr-body{font-size:.77rem;color:var(--muted);line-height:1.6}
.werr-body code{
  font-family:var(--mono);font-size:.72rem;background:var(--bg);
  padding:.05rem .3rem;border-radius:3px;color:var(--text);
}
</style>
</head>
<body>

<header>
  <div class="logo">
    <img class="logo-mark" src="__MI_LOGO__" alt="Machine Internet" width="28" height="28">
    <div class="logo-name">Machine Internet <span>UAA</span></div>
  </div>
  <div class="hdr-right">
    <div class="count-pill"><b id="hdr-count">0</b> services</div>
    <button class="icon-btn" onclick="openWiki()" title="How it works">?</button>
    <button class="icon-btn" id="theme-btn" onclick="toggleTheme()" title="Toggle theme">☀</button>
  </div>
</header>

<div class="wrap-section">
  <div class="wrap-row">
    <input class="wrap-input" type="url" id="w-url" placeholder="https://api.example.com  (paste any API URL)">
    <button class="btn-primary" id="wrap-btn" onclick="startWrap()">Wrap API</button>
    <button class="btn-ghost" id="opts-toggle" onclick="toggleOpts()">Options</button>
  </div>
  <div class="opts-panel" id="opts-panel">
    <div class="opt-field">
      <label class="opt-label" for="w-spec">OpenAPI Spec URL</label>
      <input class="opt-input" type="url" id="w-spec" placeholder="https://…/openapi.json  (optional, skips probing)">
    </div>
    <div class="opt-field">
      <label class="opt-label" for="w-tags">Filter Tags</label>
      <input class="opt-input" type="text" id="w-tags" placeholder="repos,issues  (comma-separated)">
    </div>
  </div>
</div>

<div class="body-area">
  <div class="grid-area">
    <div class="grid-hdr">
      <span class="section-label">Wrapped Services</span>
      <div class="search-wrap">
        <span class="search-icon">&#x2315;</span>
        <input id="svc-search" class="search-input" type="text" placeholder="Search services..." oninput="onSearch(this.value)">
        <button class="search-clear" id="search-clear" onclick="clearSearch()" title="Clear">&times;</button>
      </div>
    </div>
    <div class="svc-grid" id="svc-grid">
      <div class="grid-empty">
        <div class="grid-empty-icon">⬡</div>
        <div class="grid-empty-text">Loading…</div>
      </div>
    </div>
  </div>

  <div class="sidebar">
    <div class="sb-tabs">
      <button class="tab-btn active" id="tab-log"   onclick="showTab('log')">Log</button>
      <button class="tab-btn"        id="tab-evals" onclick="showTab('evals')">Evals</button>
      <button class="tab-btn"        id="tab-auth"  onclick="showTab('auth')">Auth</button>
    </div>
    <div class="tab-panel active" id="panel-log">
      <div class="log-area" id="log-area">
        <div class="log-hint">Discovery output streams here when you wrap a new API.</div>
      </div>
    </div>
    <div class="tab-panel" id="panel-evals">
      <div class="evals-area" id="evals-area"></div>
    </div>
    <div class="tab-panel" id="panel-auth">
      <div class="auth-area" id="auth-area">
        <div class="auth-hint">Tap a service card to see its details here.</div>
      </div>
    </div>
  </div>
</div>

<div class="metrics-bar">
  <div class="metric"><span class="m-num" id="m-wrapped">–</span><span class="m-lbl">Wrapped</span></div>
  <div class="metric"><span class="m-num" id="m-tools">–</span><span class="m-lbl">Tools</span></div>
  <div class="metric"><span class="m-num" id="m-live">–</span><span class="m-lbl">Live</span></div>
</div>

<footer>
  <span class="ft-label">MCP</span>
  <div class="ft-url empty" id="ft-url">Select a live service</div>
  <button class="btn-copy-ft" id="ft-copy" onclick="copyFooter()" disabled>Copy URL</button>
</footer>

<!-- ── wiki modal ─────────────────────────────────────────────────────────── -->
<div class="wiki-backdrop" id="wiki-backdrop" onclick="closeWikiOnBackdrop(event)">
  <div class="wiki-modal">
    <div class="wiki-hdr">
      <span class="wiki-title">How Machine Internet UAA Works</span>
      <button class="wiki-close" onclick="closeWiki()">✕</button>
    </div>
    <div class="wiki-body">

      <div class="wiki-section">
        <h3>What is this?</h3>
        <p><strong>Machine Internet UAA</strong> (Universal API Adapter) wraps any web API into an <strong>MCP server</strong>, a standardized interface that AI agents can call directly. Paste a URL, it discovers the API tools, generates a schema, and exposes it on a path that Claude or any MCP client can connect to.</p>
      </div>

      <div class="wiki-section">
        <h3>Discovery Paths</h3>
        <div class="wiki-path">
          <div class="wiki-path-card">
            <div class="wpc-badge wpc-a">Path A</div>
            <div class="wpc-title">OpenAPI Spec</div>
            <div class="wpc-desc">Probes 20 standard spec paths. Finds an OpenAPI / Swagger spec and converts every endpoint into an MCP tool. Works on any modern REST API that publishes a spec.</div>
          </div>
          <div class="wiki-path-card">
            <div class="wpc-badge wpc-b">Path B</div>
            <div class="wpc-title">Traffic Sniffing</div>
            <div class="wpc-desc">When no spec exists, a headless browser loads the site and records XHR/fetch calls. Works on SPAs (single-page apps). Server-rendered pages yield no captures.</div>
          </div>
        </div>
      </div>

      <div class="wiki-section">
        <h3>Step-by-step</h3>
        <div class="wiki-steps">
          <div class="wiki-step"><div class="step-num">1</div><div class="step-body"><b>Paste a URL</b> - any API homepage, docs page, or direct spec URL. Use the Options panel to provide a spec URL or filter tags if you already know them.</div></div>
          <div class="wiki-step"><div class="step-num">2</div><div class="step-body"><b>Click "Wrap API"</b> - the discovery pipeline runs (Path A first, Path B as fallback). Output streams in the Log tab in real time.</div></div>
          <div class="wiki-step"><div class="step-num">3</div><div class="step-body"><b>Start the service</b> - click Start on any card. This creates an in-process MCP server mounted at <code style="font-family:var(--mono);font-size:.75rem">/mcp/{name}</code> on this same URL.</div></div>
          <div class="wiki-step"><div class="step-num">4</div><div class="step-body"><b>Copy the MCP URL</b> - shown in the footer and on each live card. Paste it into your Claude MCP config or any compatible AI client.</div></div>
          <div class="wiki-step"><div class="step-num">5</div><div class="step-body"><b>Use in Claude</b> - add the URL under Settings &gt; MCP Servers. Claude will see all discovered tools and call them on your behalf.</div></div>
        </div>
      </div>

      <div class="wiki-section">
        <h3>MCP Config snippet</h3>
        <p>Add this block to your Claude Desktop config file (<code style="font-family:var(--mono);font-size:.78rem">claude_desktop_config.json</code>):</p>
        <div class="wiki-code">{
  "mcpServers": {
    "my-api": {
      "url": "https://your-app.onrender.com/mcp/service_name"
    }
  }
}</div>
      </div>

      <div class="wiki-section">
        <h3>Auth &amp; Secrets</h3>
        <p>Tap any service card, then open the Auth tab to see its auth type. Set secrets as environment variables before starting the server. They are read at runtime and never written into the schema file.</p>
      </div>

      <div class="wiki-section">
        <h3>Good targets for Path B</h3>
        <p>Services with no public spec, internal tools, or anything built before the OpenAPI standard. Works best on React / Vue / Angular SPAs that fire API calls on page load. Examples: search dashboards, HN Algolia, undocumented internal portals.</p>
        <p>Bad targets for Path B: bare API subdomains (api.example.com returns JSON directly, not a page), fully server-rendered sites (no XHR calls at all), sites behind authentication.</p>
      </div>

      <div class="wiki-section">
        <h3>Troubleshooting</h3>
        <p>Errors stream to the Log tab with actionable hints. Common causes:</p>
        <div class="wiki-errs">

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">No spec found and no traffic captured</span></div>
            <div class="werr-body">The URL is likely a bare REST API endpoint (e.g. <code>api.github.com</code>) with no webpage to sniff. Use the Options panel to supply a direct OpenAPI Spec URL, or point at the service homepage and enable traffic sniffing.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag info">INFO</span><span class="werr-title">No spec at standard locations for api.example.com</span></div>
            <div class="werr-body">Some services host their spec on a CDN, not on the API domain. Provide the spec URL directly in Options. For GitHub: <code>raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json</code></div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag warn">WARN</span><span class="werr-title">Spec has N endpoints, capped at 40</span></div>
            <div class="werr-body">Large specs are truncated before condensation. Use Filter Tags in Options to focus on specific areas, e.g. <code>repos,issues</code> for GitHub or <code>users,posts</code> for a social API. This reduces the endpoint count before the cap applies.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">Found a spec but could not parse it</span></div>
            <div class="werr-body">Supported formats are OpenAPI 3.x and Swagger 2.x in JSON or YAML. The file may be malformed, a different format, or require authentication to download. Try providing a different spec URL directly in Options.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">Page made no XHR calls (server-rendered)</span></div>
            <div class="werr-body">The page loaded but made no fetch or XHR requests. This is a server-rendered site. Try a more dynamic page: a search results URL, a user dashboard, or an item detail page. Adding <code>?q=test</code> to trigger a search may help.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">XHR calls made but all filtered as trackers</span></div>
            <div class="werr-body">The page only fired analytics or ad calls. Try a more interactive page such as a search results page or dashboard that fetches real data.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">XHR calls made but none matched the service brand</span></div>
            <div class="werr-body">The page called a different domain than expected. The API may be on a different TLD (e.g. website on <code>tvmaze.com</code>, API on <code>api.tvmaze.com</code>). The error shows which domains were seen. Try pointing directly at the API domain.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">AI condensation returned invalid or empty output</span></div>
            <div class="werr-body">The LLM produced unparseable JSON or no tools. Try filtering with tags to reduce endpoint count, use <code>--raw</code> to inspect the raw schema, or run again since LLM output is non-deterministic.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">AI provider quota exhausted</span></div>
            <div class="werr-body">All configured models and API keys are rate-limited. Add a backup key (<code>GEMINI_API_KEY_2</code>) or an OpenAI key (<code>OPENAI_API_KEY</code>) in your environment. The Gemini free tier resets daily at midnight PST.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">Could not reach the target URL</span></div>
            <div class="werr-body">Check the URL is correct and publicly accessible. Services behind a VPN, firewall, or authentication cannot be auto-discovered from a cloud deployment.</div>
          </div>

          <div class="wiki-err">
            <div class="werr-hdr"><span class="werr-tag fail">FAIL</span><span class="werr-title">Playwright is not installed</span></div>
            <div class="werr-body">Path B (traffic sniffing) requires Playwright with Chromium. On a local install run <code>playwright install chromium</code>. On Railway, Playwright is installed automatically via the Dockerfile. Path A (spec probing) works without it.</div>
          </div>

        </div>
      </div>

    </div>
  </div>
</div>

<script>
// ── state ──────────────────────────────────────────────────────────────────
let _schemas = [], _servers = {}, _selectedFile = null, _searchQ = '';

// ── theme ──────────────────────────────────────────────────────────────────
(function initTheme() {
  const t = localStorage.getItem('uaa-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('theme-btn').textContent = t === 'dark' ? '☀' : '☾';
})();

function toggleTheme() {
  const curr = document.documentElement.getAttribute('data-theme');
  const next = curr === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('uaa-theme', next);
  document.getElementById('theme-btn').textContent = next === 'dark' ? '☀' : '☾';
}

// ── options panel ──────────────────────────────────────────────────────────
function toggleOpts() {
  const open = document.getElementById('opts-panel').classList.toggle('open');
  document.getElementById('opts-toggle').textContent = open ? '− Options' : '+ Options';
}

// ── tabs ───────────────────────────────────────────────────────────────────
function showTab(name) {
  ['log','evals','auth'].forEach(t => {
    document.getElementById('tab-'+t).classList.toggle('active', t===name);
    document.getElementById('panel-'+t).classList.toggle('active', t===name);
  });
}

// ── refresh ────────────────────────────────────────────────────────────────
async function refresh() {
  const [schemas, srvs] = await Promise.all([
    fetch('/api/schemas').then(r=>r.json()).catch(()=>[]),
    fetch('/api/servers').then(r=>r.json()).catch(()=>({})),
  ]);
  _schemas = schemas; _servers = srvs;
  document.getElementById('hdr-count').textContent = schemas.length;
  document.getElementById('m-wrapped').textContent = schemas.length;
  document.getElementById('m-tools').textContent   = schemas.reduce((a,s)=>a+s.tool_count,0);
  document.getElementById('m-live').textContent    = Object.keys(srvs).length;
  renderGrid(schemas, srvs);
  if (_selectedFile) updateFooter(_selectedFile);
}

// ── grid ───────────────────────────────────────────────────────────────────
// srvs: {stem: {schema, mcp_path}} — maps schema filename -> stem
function liveMap(srvs) {
  const m = {};
  for (const [stem, info] of Object.entries(srvs)) m[info.schema] = stem;
  return m;
}

function onSearch(q) {
  _searchQ = q.trim().toLowerCase();
  document.getElementById('search-clear').classList.toggle('visible', !!_searchQ);
  renderGrid(_schemas, _servers);
}
function clearSearch() {
  _searchQ = '';
  document.getElementById('svc-search').value = '';
  document.getElementById('search-clear').classList.remove('visible');
  renderGrid(_schemas, _servers);
}

function renderGrid(schemas, srvs) {
  const grid = document.getElementById('svc-grid');
  const filtered = _searchQ
    ? schemas.filter(s =>
        s.service_name.toLowerCase().includes(_searchQ) ||
        (s.source_url   || '').toLowerCase().includes(_searchQ) ||
        (s.description  || '').toLowerCase().includes(_searchQ) ||
        (s.tools        || []).some(t => t.toLowerCase().includes(_searchQ))
      )
    : schemas;
  if (!schemas.length) {
    grid.innerHTML = '<div class="grid-empty"><div class="grid-empty-icon">⬡</div><div class="grid-empty-text">No services wrapped yet</div><div class="grid-empty-hint">Paste any API URL above to get started</div></div>';
    return;
  }
  if (!filtered.length) {
    grid.innerHTML = `<div class="grid-empty"><div class="grid-empty-icon">⬡</div><div class="grid-empty-text">No results for &ldquo;${esc(_searchQ)}&rdquo;</div><div class="grid-empty-hint">Try a tool name, URL, or service name</div></div>`;
    return;
  }
  const lm = liveMap(srvs);
  grid.innerHTML = filtered.map(s => {
    const stem = lm[s.file];
    const live = !!stem;
    const sel  = _selectedFile === s.file;
    const path = s.discovery_method === 'traffic' ? 'B' : 'A';
    const mcpUrl = stem ? `${window.location.origin}/mcp/${stem}` : '';

    const livePill = live
      ? `<span class="pill p-live"><span class="live-dot"></span>live</span>`
      : `<span class="pill p-stopped">stopped</span>`;

    const mcpRow = live ? `
      <div class="mcp-row">
        <span class="mcp-url">${esc(mcpUrl)}</span>
        <button class="btn-copy-sm" onclick="cp('${esc(mcpUrl)}',event)" title="Copy">⎘</button>
      </div>` : '';

    const actionBtn = live
      ? `<button class="btn-sm btn-stop" onclick="stopSrv('${esc(stem)}',event)">■ Stop</button>`
      : `<button class="btn-sm btn-start" onclick="startSrv('${esc(s.file)}',event)">▶ Start</button>`;

    return `<div class="svc-card${live?' live':''}${sel?' selected':''}" onclick="selectCard('${esc(s.file)}')">
      <div>
        <div class="svc-name">${esc(s.service_name)}</div>
        <div class="svc-url" title="${esc(s.source_url||'')}">${esc(s.source_url||'')}</div>
      </div>
      <div class="pills">
        <span class="pill ${path==='A'?'p-a':'p-b'}">Path ${path}</span>
        <span class="pill p-tools">${s.tool_count} tools</span>
        ${livePill}
      </div>
      ${mcpRow}
      <div class="card-actions">${actionBtn}</div>
    </div>`;
  }).join('');
}

// ── card selection ─────────────────────────────────────────────────────────
function selectCard(file) {
  _selectedFile = file;
  renderGrid(_schemas, _servers);
  updateFooter(file);
  const s = _schemas.find(x=>x.file===file);
  if (s) renderAuth(s);
  showTab('auth');
}

function updateFooter(file) {
  const lm = liveMap(_servers);
  const stem = lm[file];
  const el  = document.getElementById('ft-url');
  const btn = document.getElementById('ft-copy');
  if (stem) {
    const url = `${window.location.origin}/mcp/${stem}`;
    el.textContent = url;
    el.classList.remove('empty');
    btn.disabled = false;
  } else {
    const s = _schemas.find(x=>x.file===file);
    el.textContent = (s?s.service_name:'Service') + ' - not running';
    el.classList.add('empty');
    btn.disabled = true;
  }
}

function copyFooter() {
  cp(document.getElementById('ft-url').textContent, null);
}

// ── auth panel ─────────────────────────────────────────────────────────────
function renderAuth(s) {
  const lm = liveMap(_servers);
  const stem = lm[s.file];
  const mcpUrl = stem ? `${window.location.origin}/mcp/${stem}` : null;
  document.getElementById('auth-area').innerHTML = `
    <div class="auth-group"><span class="auth-label">Service</span><span class="auth-value">${esc(s.service_name)}</span></div>
    <div class="auth-group"><span class="auth-label">Auth Type</span><span class="auth-value">${esc(s.auth_type)}</span></div>
    <div class="auth-group"><span class="auth-label">Discovery</span><span class="auth-value">Path ${s.discovery_method==='traffic'?'B (traffic)':'A (OpenAPI)'}</span></div>
    <div class="auth-group"><span class="auth-label">Source URL</span><span class="auth-value" style="font-family:var(--mono);font-size:.7rem;word-break:break-all">${esc(s.source_url||'N/A')}</span></div>
    ${mcpUrl ? `<div class="auth-group"><span class="auth-label">MCP URL</span><div class="auth-cmd">${esc(mcpUrl)}</div></div>` : ''}
  `;
}

// ── start / stop ───────────────────────────────────────────────────────────
async function startSrv(file, e) {
  if(e) e.stopPropagation();
  await fetch('/api/servers', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({schema_file:file})});
  setTimeout(refresh, 600);
}
async function stopSrv(stem, e) {
  if(e) e.stopPropagation();
  await fetch(`/api/servers/${stem}`, {method:'DELETE'});
  setTimeout(refresh, 300);
}

// ── wrap / discover ────────────────────────────────────────────────────────
async function startWrap() {
  const url = document.getElementById('w-url').value.trim();
  if (!url) { document.getElementById('w-url').focus(); return; }
  const spec = document.getElementById('w-spec').value.trim();
  const tags = document.getElementById('w-tags').value.trim();

  showTab('log');
  const logArea = document.getElementById('log-area');
  logArea.innerHTML = `<div class="ll">Discovering ${esc(url)} …</div>`;

  const btn = document.getElementById('wrap-btn');
  btn.textContent = 'Running…';
  btn.disabled = true;

  const resp = await fetch('/api/discover/stream',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({url, spec_url:spec, tags}),
  });

  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';

  let gotDone = false;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value,{stream:true});
      const lines = buf.split('\n'); buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let d;
        try { d = JSON.parse(line.slice(6)); } catch(e) { continue; }
        if (d.line !== undefined) {
          const cls = d.line.includes('[OK]')     ? 'lk'
                    : d.line.includes('[FAIL]')   ? 'lf'
                    : d.line.includes('fallback') ? 'ly'
                    : 'll';
          logArea.innerHTML += `<div class="${cls}">${esc(d.line)}</div>`;
          logArea.scrollTop = logArea.scrollHeight;
        }
        if (d.done) {
          gotDone = true;
          if (d.exit_code === 0) {
            logArea.innerHTML += '<div class="lk">✓ Done</div>';
            btn.textContent = '✓ Done';
            setTimeout(()=>{ refresh(); btn.textContent='Wrap API'; btn.disabled=false; }, 1500);
          } else {
            logArea.innerHTML += '<div class="lf">✗ Discovery failed</div>';
            btn.textContent = 'Wrap API'; btn.disabled = false;
          }
        }
      }
    }
  } catch(e) {}
  if (!gotDone) {
    logArea.innerHTML += '<div class="lf">✗ Stream closed unexpectedly</div>';
    btn.textContent = 'Wrap API'; btn.disabled = false;
  }
}

document.getElementById('w-url').addEventListener('keydown',e=>{ if(e.key==='Enter') startWrap(); });

// ── evals ──────────────────────────────────────────────────────────────────
const EVALS = [
  {name:'github_api',       path:'A', model:'gemini-2.5-flash', cov:88, pass:true},
  {name:'httpbin',          path:'A', model:'gpt-4o-mini',      cov:54, pass:true},
  {name:'pet_store_service',path:'A', model:'gpt-4o-mini',      cov:100,pass:true},
  {name:'stripe_api',       path:'A', model:'gpt-4o-mini',      cov:71, pass:true},
];

(function renderEvals() {
  document.getElementById('evals-area').innerHTML = EVALS.map(e=>`
    <div>
      <div class="eval-hdr">
        <span class="eval-name">${esc(e.name)}</span>
        <span class="eval-score ${e.pass?'pass':'fail'}">${e.cov}% ${e.pass?'PASS':'FAIL'}</span>
      </div>
      <div class="eval-track"><div class="eval-bar${e.pass?'':' fail'}" style="width:${e.cov}%"></div></div>
      <div class="eval-meta">Path ${e.path} · ${esc(e.model)}</div>
    </div>
  `).join('');
})();

// ── wiki ───────────────────────────────────────────────────────────────────
function openWiki() { document.getElementById('wiki-backdrop').classList.add('open'); }
function closeWiki() { document.getElementById('wiki-backdrop').classList.remove('open'); }
function closeWikiOnBackdrop(e) { if(e.target===e.currentTarget) closeWiki(); }
document.addEventListener('keydown', e => { if(e.key==='Escape') closeWiki(); });

// ── utilities ──────────────────────────────────────────────────────────────
function cp(text, e) { if(e) e.stopPropagation(); navigator.clipboard.writeText(text).catch(()=>{}); }
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

// ── init ───────────────────────────────────────────────────────────────────
refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    SCHEMAS_DIR.mkdir(exist_ok=True)
    port = int(os.environ.get("PORT", 7000))
    uvicorn.run(wrapped_app, host="0.0.0.0", port=port, log_level="warning")
