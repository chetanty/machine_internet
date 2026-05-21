from __future__ import annotations
from typing import Any, Optional

import httpx

from ..discovery.schema import AgentTool, EndpointMapping, ParameterLocation
from ..auth.injector import AuthInjector


class ToolExecutor:
    def __init__(self, base_url: str, auth_injector: Optional[AuthInjector] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_injector = auth_injector

    async def execute(self, tool: AgentTool, arguments: dict[str, Any]) -> Any:
        if not tool.endpoint_mappings:
            return {"error": f"No endpoint mappings for '{tool.name}'"}

        mapping = self._select_mapping(tool.endpoint_mappings, arguments)
        url, query, headers, body = self._build_request(mapping, arguments)

        if self.auth_injector:
            headers, query = await self.auth_injector.inject(headers, query)

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
                                     headers={"User-Agent": "UAA/1.0 (Universal Agent Adapter)"}) as client:
            resp = await client.request(
                method=mapping.method,
                url=url,
                params=query or None,
                headers=headers or None,
                json=body or None,
            )
            resp.raise_for_status()

        try:
            result = resp.json()
        except Exception:
            result = {"text": resp.text}

        if tool.response_fields and isinstance(result, dict):
            result = {k: v for k, v in result.items() if k in tool.response_fields}

        return result

    # Maps action keyword → preferred HTTP methods, in priority order
    _ACTION_METHODS: dict[str, list[str]] = {
        "create": ["POST"],
        "add":    ["POST"],
        "get":    ["GET"],
        "fetch":  ["GET"],
        "list":   ["GET"],
        "read":   ["GET"],
        "update": ["PATCH", "PUT"],
        "edit":   ["PATCH", "PUT"],
        "patch":  ["PATCH"],
        "put":    ["PUT"],
        "delete": ["DELETE"],
        "remove": ["DELETE"],
    }

    def _select_mapping(
        self,
        mappings: list[EndpointMapping],
        arguments: dict[str, Any],
    ) -> EndpointMapping:
        # Collect candidates: mappings whose required path params are all satisfied
        candidates: list[tuple[int, EndpointMapping]] = []
        for m in mappings:
            path_params = [pm for pm in m.parameter_mappings if pm.location == ParameterLocation.PATH]
            if any(arguments.get(pm.tool_param) is None for pm in path_params):
                continue
            score = sum(1 for pm in m.parameter_mappings if arguments.get(pm.tool_param) is not None)
            candidates.append((score, m))

        if not candidates:
            return mappings[0]

        best_score = max(s for s, _ in candidates)
        finalists = [m for s, m in candidates if s == best_score]

        # If the caller supplied an `action` value, use it to pick the HTTP method
        action = str(arguments.get("action", "")).lower().strip()
        if action and len(finalists) > 1:
            preferred = self._ACTION_METHODS.get(action, [])
            for method in preferred:
                for m in finalists:
                    if m.method.upper() == method:
                        return m

        return finalists[0]

    def _build_request(
        self,
        mapping: EndpointMapping,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, str], Optional[dict[str, Any]]]:
        path = mapping.path
        query: dict[str, Any] = dict(mapping.static_params)
        headers: dict[str, str] = {}
        body: dict[str, Any] = {}

        mapped: set[str] = set()
        for pm in mapping.parameter_mappings:
            value = arguments.get(pm.tool_param)
            if value is None:
                continue
            mapped.add(pm.tool_param)
            if pm.location == ParameterLocation.PATH:
                path = path.replace(f"{{{pm.endpoint_param}}}", str(value))
            elif pm.location == ParameterLocation.QUERY:
                query[pm.endpoint_param] = value
            elif pm.location == ParameterLocation.HEADER:
                headers[pm.endpoint_param] = str(value)
            elif pm.location == ParameterLocation.BODY:
                body[pm.endpoint_param] = value

        if mapping.method in ("POST", "PUT", "PATCH"):
            for k, v in arguments.items():
                if k not in mapped and v is not None:
                    body[k] = v

        return f"{self.base_url}{path}", query, headers, body or None
