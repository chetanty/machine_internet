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
from machine_internet.serving.mcp_server import build_server  # noqa: E402

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "machine_internet" / "examples"
SCHEMAS: dict[str, CondensedSchema] = {
    f.stem: CondensedSchema(**json.loads(f.read_text(encoding="utf-8")))
    for f in sorted(EXAMPLES_DIR.glob("*.json"))
}
REPO = "https://github.com/chetanty/machine_internet"

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
"""

_JS = """
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
            "tools": [{"name": t.name, "description": t.description} for t in s.tools],
        }
        for name, s in SCHEMAS.items()
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
    first = next(iter(SCHEMAS), "bundlephobia")
    cfg = json.dumps(
        {"mcpServers": {first: {"command": "npx", "args": ["mcp-remote", f"{base}/mcp/{first}"]}}}, indent=2
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Machine Internet</title>
<meta name="description" content="Turn any website into an MCP server. No API needed.">
<style>{_CSS}</style></head><body><main>
<h1>Turn any website into an MCP server.</h1>
<p class="lede">No API needed. Machine Internet watches the hidden API calls a site makes and turns them
into tools your agent can call. None of the sites below has a public API. Here they are as live MCP endpoints.</p>
<h2>Wrap your own</h2>
<pre>uvx machine-internet wrap https://bundlephobia.com/package/react</pre>
<p class="note">Runs on your machine: a headless browser sniffs the site and an LLM condenses what it finds.
<a href="{REPO}">Source and docs on GitHub</a>.</p>
<h2>Use a hosted endpoint</h2>
<pre>{esc(cfg)}</pre>
<p class="note">Clients that support remote MCP servers can use the URL directly. Others connect through
<code>mcp-remote</code> as shown.</p>
<h2>Live endpoints</h2>
<div class="grid">{"".join(cards)}</div>
</main><script>{_JS}</script></body></html>"""
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
