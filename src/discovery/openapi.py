from __future__ import annotations
from typing import Any, Optional

import httpx

from .errors import SpecEmptyError, SpecNetworkError, SpecParseError
from .schema import (
    AuthScheme,
    Endpoint,
    EndpointParameter,
    ParameterLocation,
    RawSchema,
)

SPEC_PATHS = [
    "/openapi.json",
    "/openapi.yaml",
    "/swagger.json",
    "/swagger.yaml",
    "/api-docs",
    "/api-docs.json",
    "/api-docs.yaml",
    "/v1/openapi.json",
    "/v2/openapi.json",
    "/v2/swagger.json",
    "/v3/openapi.json",
    "/api/v3/openapi.json",
    "/api/openapi.json",
    "/api/swagger.json",
    "/docs/openapi.json",
    "/spec.json",
    "/spec/openapi.json",
    "/.well-known/openapi.json",
    "/openapi/v3/api-docs",
    "/v3/api-docs",
]


async def discover_openapi(base_url: str) -> Optional[RawSchema]:
    """Return a RawSchema or None (spec not found). Raises SpecNetworkError, SpecParseError, SpecEmptyError."""
    base_url = base_url.rstrip("/")
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        spec, spec_path = await _fetch_spec(client, base_url)
        if spec is None:
            return None
    schema = _parse_spec(spec, base_url)
    if not schema.endpoints:
        raise SpecEmptyError(spec_path or "")
    return schema


async def discover_openapi_from_spec_url(spec_url: str, base_url: str) -> RawSchema:
    """Fetch a spec from a direct URL. Raises SpecNetworkError, SpecParseError, SpecEmptyError."""
    base_url = base_url.rstrip("/")
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        try:
            resp = await client.get(spec_url)
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise SpecNetworkError(f"Could not reach {spec_url}")
        except Exception as exc:
            raise SpecParseError(spec_url) from exc
        ct = resp.headers.get("content-type", "")
        try:
            if "yaml" in ct or spec_url.endswith((".yaml", ".yml")):
                import yaml
                spec = yaml.safe_load(resp.text)
            else:
                spec = resp.json()
        except Exception as exc:
            raise SpecParseError(spec_url) from exc
    if not isinstance(spec, dict):
        raise SpecParseError(spec_url)
    schema = _parse_spec(spec, base_url)
    if not schema.endpoints:
        raise SpecEmptyError(spec_url)
    return schema


async def _fetch_spec(client: httpx.AsyncClient, base_url: str) -> tuple[Optional[dict], Optional[str]]:
    """Return (spec_dict, path_used) or (None, None). Raises SpecNetworkError, SpecParseError."""
    for path in SPEC_PATHS:
        url = f"{base_url}{path}"
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                continue
            ct = resp.headers.get("content-type", "")
            is_yaml_path = path.endswith((".yaml", ".yml"))
            is_json_path = path.endswith(".json")
            if "yaml" in ct or is_yaml_path:
                import yaml
                try:
                    data = yaml.safe_load(resp.text)
                    if isinstance(data, dict) and ("paths" in data or "openapi" in data or "swagger" in data):
                        return data, path
                except Exception:
                    if is_yaml_path:
                        raise SpecParseError(path)
                continue
            try:
                data = resp.json()
                if "paths" in data or "openapi" in data or "swagger" in data:
                    return data, path
            except Exception:
                if is_json_path:
                    raise SpecParseError(path)
        except (SpecParseError, SpecNetworkError):
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise SpecNetworkError(f"Could not reach {base_url}")
        except Exception:
            continue
    return None, None


def _parse_spec(spec: dict, base_url: str) -> RawSchema:
    info = spec.get("info", {})
    title = info.get("title", "Unknown Service")
    description = info.get("description", "")
    version = info.get("version", "1.0.0")

    if "servers" in spec and spec["servers"]:
        server_url = spec["servers"][0].get("url", "")
        if server_url.startswith("http"):
            base_url = server_url.rstrip("/")
        elif server_url.startswith("/"):
            # relative path — combine with the origin
            from urllib.parse import urlparse as _up
            _p = _up(base_url)
            base_url = f"{_p.scheme}://{_p.netloc}{server_url.rstrip('/')}"
    elif "basePath" in spec:
        base_url = base_url + spec.get("basePath", "")

    auth_schemes = _parse_auth_schemes(spec)

    endpoints: list[Endpoint] = []
    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters", [])
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            endpoints.append(_parse_operation(path, method.upper(), op, shared_params, spec))

    return RawSchema(
        base_url=base_url,
        title=title,
        description=description,
        version=version,
        endpoints=endpoints,
        auth_schemes=auth_schemes,
        discovery_method="openapi",
    )


def _parse_auth_schemes(spec: dict) -> list[AuthScheme]:
    schemes: list[AuthScheme] = []
    security_schemes: dict = (
        spec.get("components", {}).get("securitySchemes", {})
        or spec.get("securityDefinitions", {})
    )
    for scheme in security_schemes.values():
        t = scheme.get("type", "").lower()
        if t == "apikey":
            schemes.append(AuthScheme.API_KEY)
        elif t == "oauth2":
            schemes.append(AuthScheme.OAUTH2)
        elif t == "http":
            if scheme.get("scheme", "").lower() == "bearer":
                schemes.append(AuthScheme.BEARER)
            elif scheme.get("scheme", "").lower() == "basic":
                schemes.append(AuthScheme.BASIC)
    return list(set(schemes))


def _parse_operation(
    path: str,
    method: str,
    op: dict,
    shared_params: list,
    spec: dict,
) -> Endpoint:
    all_raw_params = list(shared_params) + list(op.get("parameters", []))
    parameters: list[EndpointParameter] = []

    for p in all_raw_params:
        p = _resolve_ref(p, spec)
        if not p or not p.get("name"):
            continue
        parameters.append(EndpointParameter(
            name=p["name"],
            location=_map_location(p.get("in", "query")),
            required=p.get("required", False),
            type=_extract_type(p.get("schema", p)),
            description=p.get("description", ""),
        ))

    request_body_schema: Optional[dict] = None
    if "requestBody" in op:
        content = op["requestBody"].get("content", {})
        for ct, cv in content.items():
            if "json" in ct:
                schema = _resolve_ref(cv.get("schema", {}), spec)
                request_body_schema = schema
                for prop_name, prop_schema in (schema or {}).get("properties", {}).items():
                    required_fields = (schema or {}).get("required", [])
                    parameters.append(EndpointParameter(
                        name=prop_name,
                        location=ParameterLocation.BODY,
                        required=prop_name in required_fields,
                        type=_extract_type(prop_schema),
                        description=prop_schema.get("description", ""),
                    ))
                break

    response_schema: Optional[dict] = None
    for code in ("200", "201", "default"):
        resp = _resolve_ref(op.get("responses", {}).get(code), spec)
        if not resp:
            continue
        for ct, cv in resp.get("content", {}).items():
            if "json" in ct:
                response_schema = _resolve_ref(cv.get("schema", {}), spec)
                break
        if not response_schema and "schema" in resp:
            response_schema = _resolve_ref(resp["schema"], spec)
        if response_schema:
            break

    return Endpoint(
        path=path,
        method=method,
        operation_id=op.get("operationId"),
        summary=op.get("summary", ""),
        description=op.get("description", ""),
        parameters=parameters,
        request_body_schema=request_body_schema,
        response_schema=response_schema,
        tags=op.get("tags", []),
    )


def _resolve_ref(obj: Any, spec: dict) -> Optional[dict]:
    if not isinstance(obj, dict):
        return obj
    if "$ref" not in obj:
        return obj
    parts = obj["$ref"].lstrip("#/").split("/")
    current: Any = spec
    try:
        for part in parts:
            current = current[part]
        return current
    except (KeyError, TypeError):
        return None


def _map_location(in_: str) -> ParameterLocation:
    return {
        "query": ParameterLocation.QUERY,
        "header": ParameterLocation.HEADER,
        "path": ParameterLocation.PATH,
        "body": ParameterLocation.BODY,
        "cookie": ParameterLocation.COOKIE,
    }.get(in_.lower(), ParameterLocation.QUERY)


def _extract_type(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "string"
    t = schema.get("type", "string")
    if t == "array":
        item_type = _extract_type(schema.get("items", {}))
        return f"array[{item_type}]"
    return t or "string"
