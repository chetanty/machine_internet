from __future__ import annotations
import base64
import json
import re
import time
from urllib.parse import quote
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
        key_source_url: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_injector = auth_injector
        self.static_headers = dict(static_headers or {})
        self.static_query = dict(static_query or {})
        # Page that hands out the public keys, used to fetch fresh ones when they expire
        self.key_source_url = key_source_url

    async def execute(self, tool: AgentTool, arguments: dict[str, Any]) -> Any:
        if not tool.endpoint_mappings:
            return {"error": f"No endpoint mappings for '{tool.name}'"}

        # Fill in declared defaults for anything the caller left out
        arguments = {**{p.name: p.default for p in tool.parameters if p.default is not None}, **arguments}
        mapping = self._select_mapping(tool.endpoint_mappings, arguments)

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
                                     headers={"User-Agent": "machine-internet/1.0"}) as client:
            await self._refresh_expiring_keys(client)
            for attempt in range(2):
                url, query, headers, body = self._build_request(mapping, arguments)
                if self.auth_injector:
                    headers, query = await self.auth_injector.inject(headers, query)
                resp = await client.request(
                    method=mapping.method,
                    url=url,
                    params=query or None,
                    headers=headers or None,
                    json=body if body and mapping.body_format != "form" else None,
                    data=body if body and mapping.body_format == "form" else None,
                )
                # A rejected public key may have been rotated early: fetch a fresh one and retry once
                if attempt == 0 and resp.status_code in (401, 403) and await self._refresh_expiring_keys(client, force=True):
                    continue
                break
            resp.raise_for_status()

        try:
            result = resp.json()
        except Exception:
            result = {"text": resp.text}

        if tool.response_fields and isinstance(result, (dict, list)):
            # Plain names keep top-level keys; dotted paths like "items[].post.title" reach inside
            trimmed = _project(result, tool.response_fields)
            # If none of the expected fields came back (e.g. an error body), show what did
            result = trimmed if trimmed not in (None, {}, []) else result

        # Many sites ignore a page-size parameter and return everything; honour it here
        limit = arguments.get("limit")
        if isinstance(result, list) and isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            result = result[:limit]

        return result

    async def _refresh_expiring_keys(self, client: httpx.AsyncClient, force: bool = False) -> bool:
        """Some sites hand the browser short-lived public keys (e.g. Algolia secured keys with a
        validUntil). When one is expired or rejected, read a fresh one from the site's own page."""
        if not self.key_source_url:
            return False
        stale = [(store, k, v) for store in (self.static_headers, self.static_query)
                 for k, v in store.items() if _key_expiry(v) is not None
                 and (force or _key_expiry(v) < time.time() + 300)]
        if not stale:
            return False
        changed = False
        for store, k, v in stale:
            fresh = _FRESH_KEYS.get(v)
            if fresh is None or _key_expiry(fresh) < time.time() + 300:
                fresh = await _find_fresh_key(client, self.key_source_url, v)
            if fresh and fresh != v:
                _FRESH_KEYS[v] = fresh
                store[k] = fresh
                changed = True
        return changed

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
        query: dict[str, Any] = {}
        body: dict[str, Any] = {}
        # Static values may be templates like "query={query}&page={page}"; fill them from the arguments
        templated: set[str] = set()
        has_body = mapping.method in ("POST", "PUT", "PATCH")
        for k, v in mapping.static_params.items():
            v = _fill_template(v, arguments, templated)
            if v is _MISSING:
                continue
            if _is_body_path(k) or (has_body and isinstance(v, (dict, list))):
                _set_path(body, k, v)
            else:
                query[k] = v
        # Site-wide public keys were checked at wrap time (and may be refreshed), so they win
        query.update(self.static_query)
        headers: dict[str, str] = dict(self.static_headers)

        mapped: set[str] = set(templated)
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
                _set_path(body, pm.endpoint_param, value)

        if mapping.method in ("POST", "PUT", "PATCH"):
            for k, v in arguments.items():
                if k not in mapped and v is not None:
                    body[k] = v

        missing = re.findall(r"\{(\w+)\}", path)
        if missing:
            raise ValueError(f"Missing value for URL path parameter(s): {', '.join(missing)}")
        if body and re.search(r"\.(algolia\.net|algolianet\.com)$", self.base_url.split("/")[2] if "//" in self.base_url else ""):
            _algolia_flatten(body)
        return f"{self.base_url}{path}", query, headers, body or None


# old key -> fresh key, shared across executors in the same process
_FRESH_KEYS: dict[str, str] = {}
_B64_RE = re.compile(r"[A-Za-z0-9+/]{80,}={0,2}")


def _decode_key(value: str) -> Optional[str]:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), validate=True).decode("utf-8")
    except Exception:
        return None


def _key_expiry(value: str) -> Optional[float]:
    text = _decode_key(value) if isinstance(value, str) and len(value) >= 80 else None
    m = re.search(r"validUntil=(\d+)", text or "")
    return float(m.group(1)) if m else None


def _key_scope(text: str) -> str:
    # Everything that defines what the key may access, minus the signature and expiry
    params = text[64:] if re.match(r"[0-9a-f]{64}", text) else text
    return re.sub(r"&?validUntil=\d+", "", params)


async def _find_fresh_key(client: httpx.AsyncClient, page_url: str, old: str) -> Optional[str]:
    old_text = _decode_key(old) or ""
    try:
        html = (await client.get(page_url, headers={"User-Agent": "Mozilla/5.0"})).text
    except Exception:
        return None
    best, best_exp = None, _key_expiry(old) or 0
    for cand in set(_B64_RE.findall(html)):
        text = _decode_key(cand)
        exp = _key_expiry(cand)
        if text and exp and exp > best_exp and _key_scope(text) == _key_scope(old_text):
            best, best_exp = cand, exp
    return best


_MISSING = object()
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _fill_template(value: Any, arguments: dict[str, Any], used: set[str]) -> Any:
    """Fill {name} placeholders in a static value from the tool arguments.
    A value that is exactly "{name}" takes the argument as-is (keeping its type); inside a
    query-string-like value, "key={name}" pairs whose argument is missing are dropped."""
    if isinstance(value, dict):
        out = {k: _fill_template(v, arguments, used) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not _MISSING}
    if isinstance(value, list):
        return [x for x in (_fill_template(v, arguments, used) for v in value) if x is not _MISSING]
    if not isinstance(value, str) or "{" not in value:
        return value
    whole = re.fullmatch(r"\{(\w+)\}", value)
    if whole:
        name = whole.group(1)
        if arguments.get(name) is None:
            return _MISSING
        used.add(name)
        return arguments[name]

    def text(v: Any) -> str:
        return json.dumps(v) if isinstance(v, (list, dict)) else str(v).lower() if isinstance(v, bool) else str(v)

    if "=" in value and "&" in value or re.fullmatch(r"[\w.\[\]-]+=\{\w+\}", value):
        pairs = []
        for pair in value.split("&"):
            names = _PLACEHOLDER_RE.findall(pair)
            if any(arguments.get(n) is None for n in names):
                continue
            used.update(names)
            pairs.append(_PLACEHOLDER_RE.sub(lambda m: quote(text(arguments[m.group(1)]), safe=""), pair))
        return "&".join(pairs)
    names = _PLACEHOLDER_RE.findall(value)
    if any(arguments.get(n) is None for n in names):
        return _MISSING
    used.update(names)
    return _PLACEHOLDER_RE.sub(lambda m: text(arguments[m.group(1)]), value)


# LLMs sometimes prefix body field paths with the name of the body itself
_BODY_PREFIXES = ("requestBody.", "request_body.", "body.", "payload.")
_PATH_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _is_body_path(name: str) -> bool:
    return name.startswith(_BODY_PREFIXES) or "[" in name


def _set_path(body: dict, name: str, value: Any) -> None:
    """Set body["a"]["b"][0]["c"] for a name like "a.b[0].c" (a plain name sets one key)."""
    for prefix in _BODY_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
    tokens = [int(i) if i else k for k, i in _PATH_TOKEN_RE.findall(name)]
    if len(tokens) <= 1:
        body[name] = value
        return
    cur: Any = body
    for tok, nxt in zip(tokens, tokens[1:]):
        empty: Any = [] if isinstance(nxt, int) else {}
        if isinstance(tok, int):
            while len(cur) <= tok:
                cur.append(None)
            if cur[tok] is None:
                cur[tok] = empty
            cur = cur[tok]
        else:
            cur = cur.setdefault(tok, empty)
    last = tokens[-1]
    if isinstance(last, int):
        while len(cur) <= last:
            cur.append(None)
    cur[last] = value


def _algolia_flatten(body: dict) -> None:
    """Algolia wants each request's "params" as a string or as top-level fields, never an object."""
    for req in body.get("requests") or []:
        if isinstance(req, dict) and isinstance(req.get("params"), dict):
            req.update(req.pop("params"))


_NOTHING = object()


def _pick(obj: Any, tokens: list[str]) -> Any:
    """Keep only the part of obj named by a path like ["items[]", "post", "title"]."""
    if not tokens:
        return obj
    token, rest = tokens[0], tokens[1:]
    is_list = token.endswith("[]")
    key = token[:-2] if is_list else token
    value = obj if key == "" else (obj.get(key, _NOTHING) if isinstance(obj, dict) else _NOTHING)
    if value is _NOTHING:
        return _NOTHING
    if is_list:
        if not isinstance(value, list):
            return _NOTHING
        picked = [_pick(item, rest) for item in value]
        picked = [None if p is _NOTHING else p for p in picked]
    else:
        picked = _pick(value, rest)
        if picked is _NOTHING:
            return _NOTHING
    return picked if key == "" else {key: picked}


def _merge(a: Any, b: Any) -> Any:
    if a is None or a is _NOTHING:
        return b
    if b is None or b is _NOTHING:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge(out.get(k), v)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [_merge(x, y) for x, y in zip(a, b)]
    return a


def _project(result: Any, fields: list[str]) -> Any:
    out = None
    for field in fields:
        out = _merge(out, _pick(result, field.split(".")))
    if isinstance(out, list):
        out = [x for x in out if x not in (None, {})]
    return out
