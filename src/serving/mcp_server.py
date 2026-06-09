from __future__ import annotations
import json
from typing import Any, Optional

import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp import types

from ..discovery.schema import AgentTool, CondensedSchema
from ..auth.injector import AuthInjector
from ..auth.vault import CredentialVault
from .executor import ToolExecutor

_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "boolean": "boolean",
    "number": "number",
    "object": "object",
    "array": "array",
}


def _input_schema(tool: AgentTool) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in tool.parameters:
        prop: dict[str, Any] = {
            "type": _TYPE_MAP.get(p.type, "string"),
            "description": p.description,
        }
        if p.enum:
            prop["enum"] = p.enum
        props[p.name] = prop
        if p.required:
            required.append(p.name)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def create_mcp_app(
    schema: CondensedSchema,
    vault: Optional[CredentialVault] = None,
    sse_path: str = "/mcp",
    messages_path: str = "/messages/",
    on_call=None,
) -> Any:
    """Return a pure ASGI app serving the MCP SSE protocol.

    sse_path and messages_path let callers mount at a custom prefix, e.g.
    sse_path="/mcp/github" messages_path="/mcp/github/messages/" when the app
    is served inline on a shared port rather than its own subprocess port.
    """
    server = Server(schema.service_name)

    auth_injector: Optional[AuthInjector] = None
    if vault and schema.auth_type.value != "none":
        creds = vault.get(schema.service_name)
        if creds:
            auth_injector = AuthInjector(schema.auth_type, creds)

    executor = ToolExecutor(schema.base_url, auth_injector)
    tool_map = {t.name: t for t in schema.tools}

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=t.name,
                description=t.description,
                inputSchema=_input_schema(t),
            )
            for t in schema.tools
        ]

    @server.call_tool()
    async def call_tool(
        name: str,
        arguments: Optional[dict[str, Any]],
    ) -> list[types.TextContent]:
        import time as _time
        tool = tool_map.get(name)
        if not tool:
            if on_call:
                on_call(name, False, 0)
            return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
        t0 = _time.monotonic()
        try:
            result = await executor.execute(tool, arguments or {})
            text = json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
            ok = True
        except Exception as exc:
            text = json.dumps({"error": str(exc)})
            ok = False
        duration_ms = int((_time.monotonic() - t0) * 1000)
        if on_call:
            on_call(name, ok, duration_ms)
        return [types.TextContent(type="text", text=text)]

    sse = SseServerTransport(messages_path)

    # Pure ASGI router — avoids Starlette's Route returning-None TypeError
    async def asgi_app(scope, receive, send):
        if scope["type"] == "lifespan":
            await _handle_lifespan(scope, receive, send)
            return

        path = scope.get("path", "")

        if path == sse_path and scope["type"] == "http":
            async with sse.connect_sse(scope, receive, send) as (r, w):
                await server.run(r, w, server.create_initialization_options())
            return

        if path.startswith(messages_path) and scope["type"] == "http":
            await sse.handle_post_message(scope, receive, send)
            return

        # 404 for anything else
        await send({"type": "http.response.start", "status": 404,
                    "headers": [[b"content-type", b"text/plain"]]})
        await send({"type": "http.response.body", "body": b"not found"})

    return asgi_app


async def _handle_lifespan(scope, receive, send):
    while True:
        event = await receive()
        if event["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif event["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def serve(
    schema: CondensedSchema,
    vault: Optional[CredentialVault] = None,
    host: str = "0.0.0.0",
    port: int = 8000,
) -> None:
    app = create_mcp_app(schema, vault)
    uvicorn.run(app, host=host, port=port)
