"""Deterministic fixes applied after the LLM writes the tools.

The LLM is good at naming and describing tools but unreliable at the exact request shape of
well-known backends. For those, the right shape is known, so it is rebuilt from the traffic
that was actually observed instead of trusted from the model.
"""
import re
from typing import Optional
from urllib.parse import unquote, urlparse

from ..discovery.schema import CondensedSchema, ParameterLocation, RawSchema

_ALGOLIA_HOSTS = ("algolia.net", "algolianet.com")
_MULTI_QUERY_RE = re.compile(r"^/1/indexes/([^/]+)/queries/?$")
_PARAMS_PREFIX = "requests[0].params"
_DEFAULT_HITS = 20


# Names models often get slightly wrong, mapped to the real Algolia search parameters
_FIELD_FIXES = {"filter": "filters", "facet": "facets", "facet_filter": "facetFilters", "facetFilter": "facetFilters",
                "numeric_filter": "numericFilters", "numericFilter": "numericFilters", "page_size": "hitsPerPage",
                "pageSize": "hitsPerPage", "limit": "hitsPerPage", "q": "query", "search": "query"}


def _keep_static(key: str, value: object) -> bool:
    """Drop what was copied from one observed request and would spoil every later search."""
    k = key.lower()
    if key.startswith(_PARAMS_PREFIX) or key == "requests":
        return False  # the page's params string: a snapshot, rebuilt from tool parameters instead
    if k.startswith("x-algolia"):
        return False  # keys live once at the schema level, where expiring ones get refreshed
    field = key[len("requests[0]."):] if key.startswith("requests[0].") else key
    if field in ("attributesToRetrieve", "attributesToHighlight", "attributesToSnippet") and value in ([], "[]", ""):
        return field != "attributesToRetrieve"  # an empty list there returns records with no fields
    if field == "hitsPerPage" and isinstance(value, int) and value < 5:
        return False  # facet-count requests use hitsPerPage 0 or 1
    return True


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _observed_index(raw: Optional[RawSchema]) -> Optional[str]:
    """The index name the site's own page searched, taken from the recorded request body."""
    for ep in (raw.endpoints if raw else []):
        body = (ep.request_body_schema or {}).get("observed_example")
        if not isinstance(body, dict):
            continue
        for req in body.get("requests") or []:
            if isinstance(req, dict) and req.get("indexName"):
                return str(req["indexName"])
    return None


def repair_algolia(schema: CondensedSchema, raw: Optional[RawSchema] = None) -> int:
    """Rebuild Algolia multi-query mappings into the one shape Algolia accepts. Returns how many
    mappings were changed."""
    host = urlparse(schema.base_url).netloc.lower()
    if not host.endswith(_ALGOLIA_HOSTS):
        return 0
    observed_index = _observed_index(raw)
    changed = 0
    for tool in schema.tools:
        mapped_params = {p.name for p in tool.parameters}
        for m in tool.endpoint_mappings:
            match = _MULTI_QUERY_RE.match(m.path)
            if not match or m.method.upper() != "POST":
                continue
            before = m.model_dump()
            path_index = unquote(match.group(1))
            m.path = "/1/indexes/*/queries"

            index = m.static_params.get("requests[0].indexName")
            if not isinstance(index, str) or not index:
                index = path_index if path_index != "*" else observed_index
            # The page's own params string is a snapshot of one request (often a facet-count query
            # with hitsPerPage=1), so it is dropped and rebuilt from the tool's parameters
            m.static_params = {k: v for k, v in m.static_params.items() if _keep_static(k, v)}
            if index:
                m.static_params["requests[0].indexName"] = index

            hits_mapped = False
            for pm in m.parameter_mappings:
                if pm.location != ParameterLocation.BODY:
                    continue
                target = pm.endpoint_param
                for prefix in ("request_body.", "requestBody.", "body."):
                    if target.startswith(prefix):
                        target = target[len(prefix):]
                if target == _PARAMS_PREFIX or target.startswith(_PARAMS_PREFIX):
                    field = target[len(_PARAMS_PREFIX):].lstrip(".") or _camel(pm.tool_param)
                    target = f"requests[0].{field}"
                if target.startswith("requests[0]."):
                    field = target[len("requests[0]."):]
                    target = f"requests[0].{_FIELD_FIXES.get(field, field)}"
                pm.endpoint_param = target
                hits_mapped |= target.endswith(".hitsPerPage")
            if not hits_mapped and "requests[0].hitsPerPage" not in m.static_params:
                m.static_params["requests[0].hitsPerPage"] = _DEFAULT_HITS
            if m.model_dump() != before:
                changed += 1
        tool.parameters = [p for p in tool.parameters if p.name in mapped_params]
    return changed


_WRITE_VERBS = ("update", "create", "delete", "remove", "add", "set", "save", "submit", "post", "put",
                "patch", "edit", "modify", "cancel", "checkout", "purchase", "buy", "order", "send",
                "login", "logout", "signup", "register", "subscribe", "unsubscribe", "track", "log")


def drop_write_tools(schema: CondensedSchema) -> list[str]:
    """Keep wrapped servers read-only: an agent should never change carts, accounts or orders on a
    site just because the page happened to make such a call while it was being sniffed."""
    dropped = [t.name for t in schema.tools if t.name.lower().split("_")[0] in _WRITE_VERBS]
    schema.tools = [t for t in schema.tools if t.name not in dropped]
    return dropped
