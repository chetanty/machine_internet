"""Vercel entry point: hosts every bundled example as a stateless Streamable HTTP MCP endpoint.

    GET  /                  landing page
    GET  /api/servers       JSON list of hosted servers
    POST /mcp/<name>        MCP endpoint (Streamable HTTP, stateless)

Wrapping (headless Chromium + LLM) does not run here; use the CLI for that.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager  # noqa: E402

from machine_internet.discovery.schema import CondensedSchema  # noqa: E402
from machine_internet.serving.mcp_server import _input_schema, build_server  # noqa: E402

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "machine_internet" / "examples"
SCHEMAS: dict[str, CondensedSchema] = {
    f.stem: CondensedSchema(**json.loads(f.read_text(encoding="utf-8")))
    for f in sorted(EXAMPLES_DIR.glob("*.json"))
}
REPO = "https://github.com/chetanty/machine_internet"

# Logo (180px PNG), inlined so the page needs no extra files
LOGO = "data:image/png;base64,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"

# Example arguments so "Run" works with one click in the playground
PRESETS: dict[str, dict[str, dict]] = {
    "yc_companies": {"search_companies": {"query": "robotics", "filters": "batch:\"Winter 2025\""}},
    "openrouter": {
        "get_model_details": {"slug": ["anthropic/claude-opus-5.5"]},
        "get_provider_details": {"slug": ["anthropic"]},
        "get_ranked_models": {"view": "week"},
        "get_ranked_routers": {"view": "week"},
        "get_ranked_modality_models": {"route_segment": "image", "view": "week"},
        "get_modality_category_rankings": {"route_segment": "image", "dimension": "domain"},
        "get_modality_market_share": {"route_segment": "image", "dimension": "domain", "category": "general"},
        "get_modality_chart_data": {"route_segment": "image"},
        "get_modality_category_options": {"route_segment": "image"},
    },
    "vscode_marketplace": {"search_extensions": {"search_text": "docker", "page_size": 5}},
    "luma": {
        "get_place_events": {"discover_place_api_id": "discplace-FQ4E58PeBMHGTKK", "pagination_limit": 5},
        "get_place_calendars": {"discover_place_api_id": "discplace-FQ4E58PeBMHGTKK"},
        "get_place_details": {"discover_place_api_id": "discplace-FQ4E58PeBMHGTKK"},
        "get_place_map_points": {"discover_place_api_id": "discplace-FQ4E58PeBMHGTKK"},
        "list_categories": {"pagination_limit": 10},
    },
    "bundlephobia": {
        "get_package_size": {"package": "zod"},
        "check_tree_shaking": {"package": "lodash-es"},
        "get_package_history": {"package": "react", "limit": 5},
        "get_recent_packages": {"limit": 5},
        "get_similar_packages": {"package": "moment"},
        "get_exports_sizes": {"package": "date-fns"},
    },
    "caniuse": {
        "search_features": {"search_term": "container queries"},
        "get_feature_support_data": {"feature_ids": "css-grid,css-subgrid"},
        "get_feature_details": {"feature_id": "flexbox", "data_type": "bugs"},
    },
    "npm_trends": {
        "get_package_info": {"package": "react"},
        "get_weekly_downloads": {"package": "react"},
        "get_download_trends": {"packages": "react,vue,svelte", "date_range": "2025-11-01:2026-11-01"},
        "get_github_stats": {"owner": "facebook", "repo": "react"},
    },
    "regex101": {
        "get_saved_regex": {"permalink": "vY5eL1", "version": 1},
        "list_regex_versions": {"permalink": "vY5eL1"},
    },
}

# Shown first in the playground and the landing page: the most "no API for this" examples
ORDER = ["yc_companies", "openrouter", "luma", "vscode_marketplace", "bundlephobia", "caniuse", "npm_trends", "regex101"]

_CSS = """
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4;--accent:#c2410c;--code:#f5f5f4}
@media (prefers-color-scheme:dark){:root{--bg:#0c0a09;--fg:#f5f5f4;--muted:#a8a29e;--card:#1c1917;--line:#292524;--accent:#fb923c;--code:#292524}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:960px;margin:0 auto;padding:56px 16px 80px}
h1{font-size:clamp(2rem,6vw,3.2rem);line-height:1.1;margin:0 0 12px;letter-spacing:-.02em}
.lede{font-size:1.15rem;color:var(--muted);margin:0 0 28px;max-width:640px}
code,pre{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--code);border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow-x:auto;margin:0 0 12px}
h2{font-size:1.1rem;margin:44px 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0}
.card h3{margin:0;font-size:1rem}
.card header a{color:var(--muted);font-size:.85rem;word-break:break-all;text-decoration:none}
.url{display:flex;gap:8px;align-items:center;margin:12px 0;background:var(--code);border-radius:8px;padding:6px 6px 6px 10px}
.url code{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
button{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer;font-size:.8rem}
button:hover{border-color:var(--accent)}
.tools{margin:0;padding-left:18px;color:var(--muted);font-size:.9rem}
a{color:var(--accent)}
.note{color:var(--muted);font-size:.9rem}
.brand{display:flex;align-items:center;gap:10px;font-weight:600;margin:0 0 22px;font-size:1.05rem}
.brand img{display:block}
.play{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin:0 0 8px}
.play .row{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px}
.play select,.play input{font:inherit;font-size:.95rem;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:0}
.play select{flex:1 1 220px}
.play label{display:block;font-size:.85rem;color:var(--muted);margin:10px 0 4px}
.play label b{color:var(--fg);font-family:ui-monospace,Menlo,monospace;font-weight:600}
.play .field input{width:100%}
.play .desc{color:var(--muted);font-size:.9rem;margin:2px 0 6px}
.play .run{background:var(--accent);color:#fff;border-color:var(--accent);font-size:.95rem;padding:9px 18px;margin-top:14px;font-weight:600}
.play .meta{color:var(--muted);font-size:.85rem;margin-left:10px}
.play pre{max-height:420px;margin-top:14px;white-space:pre-wrap;word-break:break-word}
.play pre.err{border-color:#dc2626}
"""

_JS = r"""
var SERVERS = JSON.parse(document.getElementById('servers-data').textContent);
var $ = function (id) { return document.getElementById(id); };
var srvSel = $('p-srv'), toolSel = $('p-tool'), form = $('p-args'), out = $('p-out'), meta = $('p-meta');
function cur() { var s = SERVERS[srvSel.selectedIndex]; return [s, s.tools[toolSel.selectedIndex]]; }
function fillTools() {
  var s = SERVERS[srvSel.selectedIndex]; toolSel.innerHTML = '';
  s.tools.forEach(function (t) { var o = document.createElement('option'); o.textContent = t.name; toolSel.appendChild(o); });
  fillForm();
}
function fillForm() {
  var t = cur()[1], sch = t.input_schema || {}, props = sch.properties || {}, req = sch.required || [];
  $('p-desc').textContent = t.description; form.innerHTML = '';
  Object.keys(props).forEach(function (k) {
    var p = props[k], d = document.createElement('div'); d.className = 'field';
    var l = document.createElement('label'), b = document.createElement('b'); b.textContent = k;
    l.appendChild(b); l.appendChild(document.createTextNode(' ' + (p.type || 'string') + (req.indexOf(k) >= 0 ? ', required' : '')));
    var i = document.createElement('input'); i.name = k; i.dataset.type = p.type || 'string';
    var ex = t.example && t.example[k] !== undefined ? t.example[k] : p['default'];
    if (ex !== undefined && ex !== null) i.value = typeof ex === 'object' ? JSON.stringify(ex) : ex;
    i.placeholder = (p.description || '').slice(0, 90);
    d.appendChild(l); d.appendChild(i); form.appendChild(d);
  });
  if (!Object.keys(props).length) { var n = document.createElement('p'); n.className = 'desc'; n.textContent = 'No parameters.'; form.appendChild(n); }
}
function args() {
  var a = {};
  form.querySelectorAll('input').forEach(function (i) {
    var v = i.value.trim(); if (v === '') return; var ty = i.dataset.type;
    if (ty === 'integer' || ty === 'number') { var n = Number(v); a[i.name] = isNaN(n) ? v : n; }
    else if (ty === 'boolean') a[i.name] = /^(true|1|yes)$/i.test(v);
    else if (ty === 'array' || ty === 'object') { try { a[i.name] = JSON.parse(v); } catch (e) { a[i.name] = ty === 'array' ? v.split(',').map(function (x) { return x.trim(); }) : v; } }
    else a[i.name] = v;
  });
  return a;
}
async function run() {
  var c = cur(), t0 = performance.now(), btn = $('p-run');
  btn.disabled = true; out.className = ''; out.textContent = 'Calling ' + c[1].name + ' ...'; meta.textContent = '';
  try {
    var r = await fetch('/mcp/' + c[0].name, { method: 'POST',
      headers: { 'content-type': 'application/json', 'accept': 'application/json, text/event-stream' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: c[1].name, arguments: args() } }) });
    var j = await r.json(), res = j.result || {}, txt = '';
    if (j.error) { txt = j.error.message || JSON.stringify(j.error); out.className = 'err'; }
    else {
      txt = (res.content || []).map(function (x) { return x.text || ''; }).join('\n');
      try { txt = JSON.stringify(JSON.parse(txt), null, 2); } catch (e) {}
      if (res.isError) out.className = 'err';
    }
    if (txt.length > 20000) txt = txt.slice(0, 20000) + '\n... (truncated)';
    out.textContent = txt || '(empty response)';
    meta.textContent = (out.className === 'err' ? 'error after ' : 'live call to the real site, ')
      + Math.round(performance.now() - t0) + ' ms';
  } catch (e) { out.className = 'err'; out.textContent = String(e); }
  btn.disabled = false;
}
SERVERS.forEach(function (s) { var o = document.createElement('option'); o.textContent = s.name + '  (' + s.source.replace(/^https?:\/\//, '').split('/')[0] + ')'; srvSel.appendChild(o); });
srvSel.addEventListener('change', fillTools); toolSel.addEventListener('change', fillForm);
$('p-run').addEventListener('click', run); fillTools();
document.querySelectorAll('button[data-copy]').forEach(function (b) {
  b.addEventListener('click', async function () {
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = 'Copied';
          setTimeout(function () { b.textContent = 'Copy'; }, 1200); } catch (e) {}
  });
});
"""


async def _send(send, status: int, body: bytes, content_type: str, extra: list | None = None) -> None:
    headers = [[b"content-type", content_type.encode()], [b"access-control-allow-origin", b"*"]]
    await send({"type": "http.response.start", "status": status, "headers": headers + (extra or [])})
    await send({"type": "http.response.body", "body": body})


def _restore_rewritten_path(scope) -> dict:
    """Vercel's catch-all rewrite turns every request path into /api/index.
    vercel.json passes the original path as ?__path=..., so put it back."""
    from urllib.parse import parse_qsl, urlencode

    params = parse_qsl((scope.get("query_string") or b"").decode(), keep_blank_values=True)
    original = next((v for k, v in params if k == "__path"), None)
    if original is None:
        return scope
    rest = urlencode([(k, v) for k, v in params if k != "__path"]).encode()
    path = "/" + original.lstrip("/")
    return dict(scope, path=path, raw_path=path.encode(), query_string=rest)


def _base_url(scope) -> str:
    headers = dict(scope.get("headers") or [])
    host = headers.get(b"x-forwarded-host") or headers.get(b"host") or b"localhost"
    proto = headers.get(b"x-forwarded-proto") or (b"https" if scope.get("scheme") == "https" else b"http")
    return f"{proto.decode()}://{host.decode()}"


def _servers_json(base: str) -> list[dict]:
    return [
        {
            "name": name,
            "url": f"{base}/mcp/{name}",
            "source": s.source_url or s.base_url,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": _input_schema(t),
                 "example": PRESETS.get(name, {}).get(t.name, {})}
                for t in s.tools
            ],
        }
        for name, s in sorted(SCHEMAS.items(), key=lambda kv: ORDER.index(kv[0]) if kv[0] in ORDER else 99)
    ]


def _landing(base: str) -> bytes:
    esc = html.escape
    cards = []
    for srv in _servers_json(base):
        tools = "".join(f"<li><code>{esc(t['name'])}</code></li>" for t in srv["tools"])
        cards.append(
            f'<article class="card"><header><h3>{esc(srv["name"])}</h3>'
            f'<a href="{esc(srv["source"])}" rel="noopener">{esc(srv["source"])}</a></header>'
            f'<div class="url"><code>{esc(srv["url"])}</code>'
            f'<button data-copy="{esc(srv["url"])}">Copy</button></div>'
            f'<ul class="tools">{tools}</ul></article>'
        )
    servers_json = json.dumps(_servers_json(base)).replace("</", "<\\/")
    first = "yc_companies" if "yc_companies" in SCHEMAS else next(iter(SCHEMAS))
    cfg = json.dumps(
        {"mcpServers": {first: {"command": "npx", "args": ["mcp-remote", f"{base}/mcp/{first}"]}}}, indent=2
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Machine Internet</title>
<link rel="icon" type="image/png" href="{LOGO}"><link rel="apple-touch-icon" href="{LOGO}">
<meta name="description" content="Turn any website into an MCP server. No API needed.">
<style>{_CSS}</style></head><body><main>
<div class="brand"><img src="{LOGO}" alt="" width="40" height="40"><span>Machine Internet</span></div>
<h1>Turn any website into an MCP server.</h1>
<p class="lede">No API needed. Machine Internet watches the hidden API calls a site makes and turns them
into tools your agent can call. None of the sites below offers a public API for this data.
Try them right here, or connect your agent to the live MCP endpoints.</p>
<h2>Try it right here</h2>
<div class="play">
  <div class="row"><select id="p-srv" aria-label="Server"></select><select id="p-tool" aria-label="Tool"></select></div>
  <p class="desc" id="p-desc"></p>
  <div id="p-args"></div>
  <button class="run" id="p-run">Run tool</button><span class="meta" id="p-meta"></span>
  <pre id="p-out">Pick a site and a tool, then press Run. This calls the same MCP endpoint your agent would.</pre>
</div>
<h2>Wrap your own</h2>
<pre>uvx machine-internet wrap https://www.ycombinator.com/companies</pre>
<p class="note">Runs on your machine: a headless browser sniffs the site and an LLM condenses what it finds.
Needs <a href="https://docs.astral.sh/uv/">uv</a> and one LLM key
(<a href="https://aistudio.google.com/apikey">Gemini's is free</a>).
<a href="{REPO}">Source and docs on GitHub</a>.</p>
<h2>Use a hosted endpoint</h2>
<pre>{esc(cfg)}</pre>
<p class="note">Clients that support remote MCP servers can use the URL directly. Others connect through
<code>mcp-remote</code> as shown.</p>
<h2>Live endpoints</h2>
<div class="grid">{"".join(cards)}</div>
</main><script type="application/json" id="servers-data">{servers_json}</script><script>{_JS}</script></body></html>"""
    return page.encode()


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return

    scope = _restore_rewritten_path(scope)
    path = scope.get("path", "/").rstrip("/") or "/"
    method = scope.get("method", "GET")

    if method == "OPTIONS":
        await _send(send, 204, b"", "text/plain", [
            [b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"],
            [b"access-control-allow-headers",
             b"content-type, accept, mcp-session-id, mcp-protocol-version, authorization"],
        ])
        return

    if path == "/":
        await _send(send, 200, _landing(_base_url(scope)), "text/html; charset=utf-8")
        return

    if path == "/api/servers":
        body = json.dumps(_servers_json(_base_url(scope)), indent=2).encode()
        await _send(send, 200, body, "application/json")
        return

    if path.startswith("/mcp/"):
        name = path[len("/mcp/"):]
        schema = SCHEMAS.get(name)
        if not schema:
            body = json.dumps({"error": f"unknown server '{name}'", "available": list(SCHEMAS)}).encode()
            await _send(send, 404, body, "application/json")
            return
        # Serverless: nothing is shared between requests, so each one gets a fresh stateless manager
        manager = StreamableHTTPSessionManager(app=build_server(schema), stateless=True, json_response=True)
        async with manager.run():
            await manager.handle_request(dict(scope, path="/mcp"), receive, send)
        return

    await _send(send, 404, b"not found", "text/plain")
