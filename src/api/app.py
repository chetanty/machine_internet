from __future__ import annotations
import asyncio
import json
import multiprocessing
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..discovery.openapi import discover_openapi
from ..discovery.traffic import discover_via_traffic
from ..condensation.condenser import condense
from ..discovery.schema import CondensedSchema
from ..auth.vault import CredentialVault
from ..config import settings

SCHEMAS_DIR = Path("schemas")
SCHEMAS_DIR.mkdir(exist_ok=True)

_vault = CredentialVault()
_running_servers: dict[str, dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for info in _running_servers.values():
        proc: Optional[multiprocessing.Process] = info.get("process")
        if proc and proc.is_alive():
            proc.terminate()


app = FastAPI(title="Universal Agent Adapter API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class DiscoverRequest(BaseModel):
    url: str
    force_traffic: bool = False


class CredentialRequest(BaseModel):
    service_name: str
    credentials: dict[str, Any]


class TestToolRequest(BaseModel):
    schema_id: str
    tool_name: str
    arguments: dict[str, Any] = {}


@app.post("/api/discover")
async def api_discover(req: DiscoverRequest):
    raw = None

    if not req.force_traffic:
        raw = await discover_openapi(req.url)

    if raw is None:
        raw = await discover_via_traffic(req.url)

    if raw is None:
        raise HTTPException(status_code=422, detail="Discovery failed for the given URL")

    condensed = await condense(raw, source_url=req.url)

    schema_file = SCHEMAS_DIR / f"{condensed.service_name}.json"
    schema_file.write_text(condensed.model_dump_json(indent=2))

    return {
        "service_name": condensed.service_name,
        "tool_count": len(condensed.tools),
        "tools": [{"name": t.name, "description": t.description} for t in condensed.tools],
        "schema_file": str(schema_file),
        "discovery_method": raw.discovery_method,
    }


@app.get("/api/schemas")
async def list_schemas():
    schemas = []
    for f in sorted(SCHEMAS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            serving = f.stem in _running_servers
            schemas.append({
                "id": f.stem,
                "service_name": data.get("service_name", f.stem),
                "service_description": data.get("service_description", ""),
                "tool_count": len(data.get("tools", [])),
                "auth_type": data.get("auth_type", "none"),
                "source_url": data.get("source_url", ""),
                "created_at": data.get("created_at", ""),
                "serving": serving,
                "serve_port": _running_servers.get(f.stem, {}).get("port"),
            })
        except Exception:
            pass
    return schemas


@app.get("/api/schemas/{schema_id}")
async def get_schema(schema_id: str):
    path = SCHEMAS_DIR / f"{schema_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Schema not found")
    data = json.loads(path.read_text())
    serving = schema_id in _running_servers
    data["serving"] = serving
    data["serve_port"] = _running_servers.get(schema_id, {}).get("port")
    return data


@app.delete("/api/schemas/{schema_id}")
async def delete_schema(schema_id: str):
    path = SCHEMAS_DIR / f"{schema_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Schema not found")
    if schema_id in _running_servers:
        proc = _running_servers[schema_id].get("process")
        if proc and proc.is_alive():
            proc.terminate()
        del _running_servers[schema_id]
    path.unlink()
    return {"deleted": schema_id}


@app.post("/api/schemas/{schema_id}/serve")
async def start_serving(schema_id: str, background_tasks: BackgroundTasks):
    path = SCHEMAS_DIR / f"{schema_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Schema not found")

    if schema_id in _running_servers and _running_servers[schema_id].get("process", None) and \
            _running_servers[schema_id]["process"].is_alive():
        return {"port": _running_servers[schema_id]["port"], "already_running": True}

    port = _next_port()
    condensed = CondensedSchema(**json.loads(path.read_text()))

    def _run():
        from ..serving.mcp_server import serve
        from ..auth.vault import CredentialVault
        serve(condensed, CredentialVault(), host="0.0.0.0", port=port)

    proc = multiprocessing.Process(target=_run, daemon=True)
    proc.start()
    _running_servers[schema_id] = {"process": proc, "port": port}

    return {"port": port, "mcp_url": f"http://localhost:{port}/mcp"}


@app.post("/api/schemas/{schema_id}/stop")
async def stop_serving(schema_id: str):
    if schema_id not in _running_servers:
        return {"stopped": False}
    info = _running_servers.pop(schema_id)
    proc = info.get("process")
    if proc and proc.is_alive():
        proc.terminate()
    return {"stopped": True}


@app.post("/api/credentials")
async def store_credentials(req: CredentialRequest):
    _vault.store(req.service_name, req.credentials)
    return {"stored": req.service_name}


@app.post("/api/tools/test")
async def test_tool(req: TestToolRequest):
    path = SCHEMAS_DIR / f"{req.schema_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Schema not found")

    condensed = CondensedSchema(**json.loads(path.read_text()))
    tool = next((t for t in condensed.tools if t.name == req.tool_name), None)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{req.tool_name}' not found")

    from ..serving.executor import ToolExecutor
    from ..auth.injector import AuthInjector

    auth_injector = None
    if condensed.auth_type.value != "none":
        creds = _vault.get(condensed.service_name)
        if creds:
            auth_injector = AuthInjector(condensed.auth_type, creds)

    executor = ToolExecutor(condensed.base_url, auth_injector)
    try:
        result = await executor.execute(tool, req.arguments)
        return {"success": True, "result": result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _next_port() -> int:
    used = {v["port"] for v in _running_servers.values()}
    port = 8100
    while port in used:
        port += 1
    return port


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port)
