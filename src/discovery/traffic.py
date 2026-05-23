from __future__ import annotations
import asyncio
import json
import re
from typing import Optional
from urllib.parse import urlparse

_TRACKER_RE = re.compile(
    r"(google-analytics|googletagmanager|doubleclick|facebook\.net|"
    r"analytics|tracking|telemetry|segment\.io|mixpanel|amplitude|"
    r"hotjar|intercom|onetrust|moatads|pubmatic|adnxs|criteo|"
    r"taboola|outbrain|bidswitch|scorecard|quantserve|adsystem|"
    r"cdn\.jsdelivr|unpkg\.com|cdnjs\.cloudflare)",
    re.IGNORECASE,
)


def _root_domain(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _brand(url: str) -> str:
    """Second-to-last domain label — the company/product name.
    Matches algolia.com and algolia.net, tvmaze.com and api.tvmaze.com, etc."""
    host = urlparse(url).netloc.lower().split(":")[0]
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host

from .errors import (
    AllTrackersFilteredError,
    BrandFilteredError,
    NoXHRCapturedError,
    PlaywrightNotInstalledError,
    TrafficNetworkError,
)
from .schema import (
    AuthScheme,
    Endpoint,
    EndpointParameter,
    ParameterLocation,
    RawSchema,
)
from ..ai.client import get_gemini_client


class _TrafficEntry:
    def __init__(
        self,
        method: str,
        url: str,
        request_body: Optional[str],
        response_body: Optional[str],
        status_code: int,
    ) -> None:
        self.method = method
        self.url = url
        self.request_body = request_body
        self.response_body = response_body
        self.status_code = status_code


async def discover_via_traffic(url: str, timeout: int = 30) -> Optional[RawSchema]:
    """Return a RawSchema or raise a typed DiscoveryError."""
    entries, stats = await _capture_traffic(url, timeout)
    if not entries:
        total = stats["total_xhr"]
        trackers = stats["tracker_blocked"]
        brands = stats["brand_blocked"]
        base_brand = _brand(url)
        if total == 0:
            raise NoXHRCapturedError()
        if brands and not trackers:
            raise BrandFilteredError(base_brand, sorted(set(brands)))
        if trackers and not brands:
            raise AllTrackersFilteredError()
        if brands:
            raise BrandFilteredError(base_brand, sorted(set(brands)))
        raise AllTrackersFilteredError()
    print(f"  [traffic] {len(entries)} same-domain XHR calls captured")
    return await _infer_schema(url, entries)


async def _capture_traffic(url: str, timeout: int) -> tuple[list[_TrafficEntry], dict]:
    """Return (entries, stats). stats keys: total_xhr, tracker_blocked, brand_blocked (list of brands)."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise PlaywrightNotInstalledError()

    entries: list[_TrafficEntry] = []
    stats: dict = {"total_xhr": 0, "tracker_blocked": 0, "brand_blocked": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        base_brand = _brand(url)

        async def on_response(response):
            try:
                rt = response.request.resource_type
                rurl = response.url
                ct = response.headers.get("content-type", "")
                if rt not in ("xhr", "fetch"):
                    return
                stats["total_xhr"] += 1
                resp_brand = _brand(rurl)
                if resp_brand != base_brand:
                    stats["brand_blocked"].append(resp_brand)
                    return
                if _TRACKER_RE.search(rurl):
                    stats["tracker_blocked"] += 1
                    return
                if "json" not in ct:
                    return
                try:
                    body = (await response.body()).decode("utf-8", errors="ignore")[:4000]
                except Exception:
                    body = None
                try:
                    req_body = response.request.post_data
                except Exception:
                    req_body = None
                entries.append(_TrafficEntry(
                    method=response.request.method,
                    url=response.url,
                    request_body=req_body,
                    response_body=body,
                    status_code=response.status,
                ))
            except Exception:
                pass

        page.on("response", on_response)

        network_error: Optional[str] = None
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            await asyncio.sleep(2)
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await asyncio.sleep(0.8)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(1)
            await _interact(page)
            await asyncio.sleep(4)
        except Exception as exc:
            err_str = str(exc)
            if any(k in err_str for k in ("ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT")):
                network_error = err_str

        await browser.close()

    if network_error is not None and stats["total_xhr"] == 0:
        raise TrafficNetworkError(url)

    return entries, stats


async def _interact(page) -> None:
    # Broader selector set — hits nav, sidebar links, tab panels, demo buttons
    selectors = (
        "nav a", "header a", "[role='tab']",
        "aside a", ".sidebar a", "[data-tab]",
        "button:not([type='submit']):not([disabled])",
        "a[href^='/']:not([href='/']):not([href^='/#'])",
    )
    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements[:5]:
                try:
                    await el.click(timeout=1500)
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
        except Exception:
            pass


async def _infer_schema(base_url: str, traffic: list[_TrafficEntry]) -> Optional[RawSchema]:
    seen: set[str] = set()
    unique: list[_TrafficEntry] = []
    for e in traffic:
        parsed = urlparse(e.url)
        path = re.sub(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "/{id}", parsed.path)
        path = re.sub(r"/\d+", "/{id}", path)
        key = f"{e.method}:{path}"
        if key not in seen:
            seen.add(key)
            unique.append(e)

    summary: list[dict] = []
    for e in unique[:40]:
        item: dict = {"method": e.method, "url": e.url, "status": e.status_code}
        if e.request_body:
            try:
                item["request_body"] = json.loads(e.request_body)
            except Exception:
                item["request_body"] = e.request_body[:400]
        if e.response_body:
            try:
                item["response_sample"] = _truncate(json.loads(e.response_body))
            except Exception:
                item["response_body"] = e.response_body[:400]
        summary.append(item)

    prompt = f"""Analyse real API network traffic captured from {base_url} and infer a clean API schema.

Traffic (already filtered to same-domain XHR/fetch calls only):
{json.dumps(summary, indent=2)}

Return JSON only:
{{
  "title": "Service name",
  "description": "What this service does",
  "base_url": "https://api.example.com",
  "auth_schemes": [],
  "endpoints": [
    {{
      "path": "/path/{{id}}",
      "method": "GET",
      "summary": "...",
      "description": "...",
      "parameters": [
        {{"name": "x", "location": "query", "required": false, "type": "string", "description": ""}}
      ]
    }}
  ]
}}

Rules:
- Only include endpoints that look like real data API calls (returning structured data).
- Skip analytics, tracking, metrics, or configuration payloads.
- Normalise numeric/UUID path segments to {{id}} or a descriptive name.
- Infer parameter types from observed values.
- Return ONLY valid JSON, no commentary."""

    raw = await get_gemini_client().generate(prompt, max_tokens=4096)
    try:
        data = _extract_json(raw)
    except (ValueError, json.JSONDecodeError):
        return None

    _LOC_NORM = {
        "body_json": "body", "json": "body", "form": "body",
        "formdata": "body", "body_form": "body", "requestbody": "body",
    }

    endpoints: list[Endpoint] = []
    for ep in data.get("endpoints", []):
        params = []
        for p in ep.get("parameters", []):
            raw_loc = str(p.get("location", "query")).lower().replace("-", "_")
            loc_str = _LOC_NORM.get(raw_loc, raw_loc)
            try:
                loc = ParameterLocation(loc_str)
            except ValueError:
                loc = ParameterLocation.QUERY
            params.append(EndpointParameter(
                name=p.get("name", ""),
                location=loc,
                required=p.get("required", False),
                type=p.get("type", "string"),
                description=p.get("description", ""),
            ))
        endpoints.append(Endpoint(
            path=ep.get("path", ""),
            method=ep.get("method", "GET"),
            summary=ep.get("summary", ""),
            description=ep.get("description", ""),
            parameters=params,
        ))

    auth_schemes: list[AuthScheme] = []
    for s in data.get("auth_schemes", []):
        try:
            auth_schemes.append(AuthScheme(s))
        except ValueError:
            pass

    return RawSchema(
        base_url=data.get("base_url", base_url),
        title=data.get("title", "Unknown Service"),
        description=data.get("description", ""),
        endpoints=endpoints,
        auth_schemes=auth_schemes,
        discovery_method="traffic",
    )


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON in response")
    end = _find_json_end(text, start)
    if end == -1:
        raise ValueError("Truncated JSON")
    return json.loads(text[start:end + 1])


def _find_json_end(text: str, start: int) -> int:
    i, depth, in_string, escape = start, 0, False, False
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


def _truncate(obj, depth: int = 2) -> object:
    if depth == 0:
        return "..."
    if isinstance(obj, dict):
        return {k: _truncate(v, depth - 1) for k, v in list(obj.items())[:8]}
    if isinstance(obj, list):
        return [_truncate(obj[0], depth - 1)] if obj else []
    if isinstance(obj, str) and len(obj) > 150:
        return obj[:150] + "..."
    return obj
