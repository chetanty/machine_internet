from __future__ import annotations
import re
from typing import Any, Optional

import httpx

from ..discovery.schema import AgentTool, EndpointMapping, ParameterLocation
from ..auth.injector import AuthInjector


class ToolExecutor:
    def __init__(
        self,
        base_url: str,
        auth_injector: Optional[AuthInjector] = None,
        static_headers: Optional[dict[str, str]] = None,
        static_query: Optional[dict[str, str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_injector = auth_injector
        self.static_headers = static_headers or {}
        self.static_query = static_query or {}

    async def execute(self, tool: AgentTool, arguments: dict[str, Any]) -> Any:
        if not tool.endpoint_mappings:
            return {"error": f"No endpoint mappings for '{tool.name}'"}

        # Fill in declared defaults for anything the caller left out
        arguments = {**{p.name: p.default for p in tool.parameters if p.default is not None}, **arguments}
        mapping = self._select_mapping(tool.endpoint_mappings, arguments)
        url, query, headers, body = self._build_request(mapping, arguments)

        if self.auth_injector:
            headers, query = await self.auth_injector.inject(headers, query)

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
                                     headers={"User-Agent": "machine-internet/1.0"}) as client:
            resp = await client.request(
                method=mapping.method,
                url=url,
                params=query or None,
                headers=headers or None,
                json=body if body and mapping.body_format != "form" else None,
                data=body if body and mapping.body_format == "form" else None,
            )
            resp.raise_for_status()

        try:
            result = resp.json()
        except Exception:
            result = {"text": resp.text}

        if tool.response_fields and isinstance(result, dict):
            trimmed = {k: v for k, v in result.items() if k in tool.response_fields}
            # If none of the expected fields came back (e.g. an error body), show what did
            result = trimmed or result

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
        query: dict[str, Any] = {**self.static_query, **mapping.static_params}
        headers: dict[str, str] = dict(self.static_headers)
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

        missing = re.findall(r"\{(\w+)\}", path)
        if missing:
            raise ValueError(f"Missing value for URL path parameter(s): {', '.join(missing)}")
        return f"{self.base_url}{path}", query, headers, body or None
