from __future__ import annotations
import json
import re
from datetime import datetime, timezone

from ..discovery.errors import LLMEmptySchemaError, LLMInvalidResponseError, LLMQuotaError
from ..discovery.schema import (
    AgentTool,
    AuthScheme,
    CondensedSchema,
    EndpointParameter,
    EndpointMapping,
    ParameterLocation,
    ParameterMapping,
    RawSchema,
    ToolParameter,
)
from ..ai.client import get_ai_client

_SYSTEM = """You are an expert API designer specialising in making APIs accessible to AI agents.

Given a raw API schema, collapse its endpoints into a minimal, high-quality set of agent tools.

Principles:
- 10–15 tools maximum. Fewer is better.
- Each tool represents a meaningful agent action, not an HTTP endpoint.
- Collapse related CRUD into unified tools where sensible.
- Name tools with verb_noun: get_customer, create_order, search_products.
- Write descriptions that tell agents exactly when to use the tool.
- Drop internal/system parameters agents don't control."""


def _extract_json(text: str) -> dict:
    """Extract the first valid JSON object from a model response."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response")

    end = _find_json_end(text, start)
    if end == -1:
        raise ValueError(f"Truncated JSON in model response: {text[:200]}")

    return json.loads(text[start:end + 1])


def _find_json_end(text: str, start: int) -> int:
    """Walk text from `start`, tracking string literals so braces inside strings are ignored."""
    i = start
    depth = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


_MAX_ENDPOINTS = 40


async def condense(raw: RawSchema, source_url: str = "") -> CondensedSchema:
    endpoints = raw.endpoints[:_MAX_ENDPOINTS]

    endpoints_data = [
        {
            "path": ep.path,
            "method": ep.method,
            "summary": ep.summary,
            "description": ep.description,
            "tags": ep.tags,
            "parameters": [
                {
                    "name": p.name,
                    "location": p.location.value,
                    "required": p.required,
                    "type": p.type,
                    "description": p.description,
                }
                for p in ep.parameters
            ],
        }
        for ep in endpoints
    ]

    prompt = f"""Service: {raw.title}
Description: {raw.description}
Base URL: {raw.base_url}
Endpoint count: {len(endpoints)}

Endpoints:
{json.dumps(endpoints_data, indent=2)}

Return condensed schema as JSON only:
{{
  "service_name": "snake_case_name",
  "service_description": "One sentence describing this service.",
  "tools": [
    {{
      "name": "verb_noun",
      "description": "Precise description for agent.",
      "parameters": [
        {{"name": "p", "type": "string", "required": true, "description": "..."}}
      ],
      "endpoint_mappings": [
        {{
          "method": "GET",
          "path": "/api/path/{{id}}",
          "parameter_mappings": [
            {{"tool_param": "p", "endpoint_param": "real_p", "location": "path"}}
          ],
          "static_params": {{}}
        }}
      ],
      "response_fields": null
    }}
  ]
}}

Return ONLY valid JSON. Maximum 15 tools."""

    try:
        raw_text = await get_ai_client().generate(
            prompt,
            system=_SYSTEM,
            max_tokens=32768,
        )
    except RuntimeError as exc:
        if "exhausted" in str(exc).lower():
            raise LLMQuotaError() from exc
        raise

    try:
        data = _extract_json(raw_text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise LLMInvalidResponseError() from exc

    tools: list[AgentTool] = []
    for t in data.get("tools", []):
        # One malformed tool from the LLM should not sink the whole run
        try:
            tools.append(_build_tool(t))
        except (KeyError, TypeError, ValueError) as exc:
            print(f"  [condense] skipped malformed tool {t.get('name', '?') if isinstance(t, dict) else '?'}: {str(exc)[:100]}")

    tools = [_repair_tool(t, raw) for t in tools]
    if not tools:
        raise LLMEmptySchemaError()

    auth_type = raw.auth_schemes[0] if raw.auth_schemes else AuthScheme.NONE

    return CondensedSchema(
        base_url=raw.base_url,
        service_name=data.get("service_name", raw.title.lower().replace(" ", "_")),
        service_description=data.get("service_description", raw.description),
        tools=tools,
        auth_type=auth_type,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_url=source_url,
        static_headers=raw.static_headers,
        static_query=raw.static_query,
    )


def _build_tool(t: dict) -> AgentTool:
    params = [
        ToolParameter(
            name=p["name"],
            type=p.get("type", "string"),
            required=p.get("required", False),
            description=p.get("description", ""),
            default=p.get("default"),
        )
        for p in t.get("parameters", [])
    ]
    mappings = [
        EndpointMapping(
            method=em["method"],
            path=em["path"],
            parameter_mappings=[
                ParameterMapping(
                    tool_param=pm["tool_param"],
                    endpoint_param=pm["endpoint_param"],
                    location=ParameterLocation(pm.get("location", "query")),
                )
                for pm in em.get("parameter_mappings", [])
            ],
            static_params=em.get("static_params", {}),
        )
        for em in t.get("endpoint_mappings", [])
    ]
    response_fields = t.get("response_fields")
    if isinstance(response_fields, list):
        response_fields = [f["name"] if isinstance(f, dict) and "name" in f else str(f) for f in response_fields]
    else:
        response_fields = None
    return AgentTool(
        name=t["name"],
        description=t.get("description", ""),
        parameters=params,
        endpoint_mappings=mappings,
        response_fields=response_fields or None,
    )


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _repair_tool(tool: AgentTool, raw: RawSchema) -> AgentTool:
    """Fix the most common LLM slip: a {placeholder} in a path that no tool parameter fills.

    If discovery observed a value for it, bake that value into the path. Otherwise expose
    it as a required tool parameter so the agent can supply it, instead of calling a URL
    with a literal "{placeholder}" in it.
    """
    raw_params: dict[tuple[str, str], dict[str, EndpointParameter]] = {
        (ep.method.upper(), ep.path): {p.name: p for p in ep.parameters} for ep in raw.endpoints
    }
    raw_formats = {(ep.method.upper(), ep.path): ep.body_format for ep in raw.endpoints}
    for m in tool.endpoint_mappings:
        m.body_format = raw_formats.get((m.method.upper(), m.path), m.body_format)
    tool_param_names = {p.name for p in tool.parameters}
    for m in tool.endpoint_mappings:
        filled = {pm.endpoint_param for pm in m.parameter_mappings if pm.location == ParameterLocation.PATH}
        for name in _PLACEHOLDER_RE.findall(m.path):
            if name in filled:
                continue
            observed = raw_params.get((m.method.upper(), m.path), {}).get(name)
            if observed is not None and observed.default not in (None, ""):
                m.path = m.path.replace("{" + name + "}", str(observed.default))
                continue
            if name not in tool_param_names:
                desc = observed.description if observed and observed.description else f"Value for {{{name}}} in the URL path."
                tool.parameters.append(ToolParameter(name=name, type="string", required=True, description=desc))
                tool_param_names.add(name)
            m.parameter_mappings.append(
                ParameterMapping(tool_param=name, endpoint_param=name, location=ParameterLocation.PATH)
            )
    return tool
