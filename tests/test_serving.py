import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from machine_internet.discovery.schema import (
    AgentTool,
    CondensedSchema,
    EndpointMapping,
    ParameterLocation,
    ParameterMapping,
    ToolParameter,
    AuthScheme,
)
from machine_internet.serving.executor import ToolExecutor


def _make_schema() -> CondensedSchema:
    tool = AgentTool(
        name="get_user",
        description="Get a user by ID.",
        parameters=[
            ToolParameter(name="user_id", type="integer", required=True, description="User ID"),
        ],
        endpoint_mappings=[
            EndpointMapping(
                method="GET",
                path="/users/{user_id}",
                parameter_mappings=[
                    ParameterMapping(tool_param="user_id", endpoint_param="user_id", location=ParameterLocation.PATH),
                ],
            )
        ],
    )
    return CondensedSchema(
        base_url="https://api.example.com",
        service_name="example_api",
        service_description="Example API",
        tools=[tool],
        auth_type=AuthScheme.NONE,
    )


@pytest.mark.asyncio
async def test_executor_builds_correct_url():
    schema = _make_schema()
    tool = schema.tools[0]
    executor = ToolExecutor(schema.base_url)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": 42, "name": "Alice"}
    mock_resp.raise_for_status = MagicMock()

    captured_url = None

    async def mock_request(method, url, **kwargs):
        nonlocal captured_url
        captured_url = url
        return mock_resp

    with patch("machine_internet.serving.executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=mock_request)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_cls.return_value = mock_client

        result = await executor.execute(tool, {"user_id": 42})

    assert captured_url == "https://api.example.com/users/42"
    assert result["name"] == "Alice"


@pytest.mark.asyncio
async def test_executor_query_params():
    tool = AgentTool(
        name="search",
        description="Search",
        parameters=[
            ToolParameter(name="q", type="string", required=True, description="Query"),
            ToolParameter(name="limit", type="integer", required=False, description="Limit"),
        ],
        endpoint_mappings=[
            EndpointMapping(
                method="GET",
                path="/search",
                parameter_mappings=[
                    ParameterMapping(tool_param="q", endpoint_param="q", location=ParameterLocation.QUERY),
                    ParameterMapping(tool_param="limit", endpoint_param="limit", location=ParameterLocation.QUERY),
                ],
            )
        ],
    )
    executor = ToolExecutor("https://api.example.com")
    url, query, headers, body = executor._build_request(tool.endpoint_mappings[0], {"q": "hello", "limit": 10})

    assert url == "https://api.example.com/search"
    assert query["q"] == "hello"
    assert query["limit"] == 10
    assert body is None


def test_executor_response_field_filtering():
    schema = _make_schema()
    tool = schema.tools[0]
    tool.response_fields = ["id", "name"]
    executor = ToolExecutor(schema.base_url)

    full_response = {"id": 1, "name": "Bob", "email": "bob@example.com", "role": "admin"}
    filtered = {k: v for k, v in full_response.items() if k in tool.response_fields}
    assert set(filtered.keys()) == {"id", "name"}
    assert "email" not in filtered


async def _capture_request(executor, tool, args):
    """Run executor.execute against a mocked HTTP client and return the request kwargs."""
    seen = {}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": True}
    mock_resp.raise_for_status = MagicMock()

    async def mock_request(method, url, **kwargs):
        seen.update(method=method, url=url, **kwargs)
        return mock_resp

    with patch("machine_internet.serving.executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=mock_request)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_cls.return_value = mock_client
        await executor.execute(tool, args)
    return seen


@pytest.mark.asyncio
async def test_form_body_and_static_keys():
    tool = AgentTool(name="support", description="d",
                     parameters=[ToolParameter(name="ids", required=True)],
                     endpoint_mappings=[EndpointMapping(
                         method="POST", path="/data.php", body_format="form",
                         static_params={"type": "support"},
                         parameter_mappings=[ParameterMapping(tool_param="ids", endpoint_param="feats",
                                                              location=ParameterLocation.BODY)])])
    ex = ToolExecutor("https://site.example", static_headers={"x-app-key": "pub"}, static_query={"k": "1"})
    seen = await _capture_request(ex, tool, {"ids": "css-grid"})
    assert seen["data"] == {"feats": "css-grid"} and seen["json"] is None
    assert seen["params"] == {"k": "1", "type": "support"}
    assert seen["headers"] == {"x-app-key": "pub"}


@pytest.mark.asyncio
async def test_parameter_default_fills_path():
    tool = AgentTool(name="get", description="d",
                     parameters=[ToolParameter(name="slug", required=True), ToolParameter(name="version", type="integer", default=1)],
                     endpoint_mappings=[EndpointMapping(method="GET", path="/r/{slug}/{version}", parameter_mappings=[
                         ParameterMapping(tool_param="slug", endpoint_param="slug", location=ParameterLocation.PATH),
                         ParameterMapping(tool_param="version", endpoint_param="version", location=ParameterLocation.PATH)])])
    seen = await _capture_request(ToolExecutor("https://site.example"), tool, {"slug": "abc"})
    assert seen["url"] == "https://site.example/r/abc/1"


@pytest.mark.asyncio
async def test_unfilled_path_placeholder_is_a_clear_error():
    tool = AgentTool(name="get", description="d", parameters=[],
                     endpoint_mappings=[EndpointMapping(method="GET", path="/r/{slug}")])
    with pytest.raises(ValueError, match="slug"):
        await _capture_request(ToolExecutor("https://site.example"), tool, {})


@pytest.mark.asyncio
async def test_nested_body_paths_and_site_keys_win():
    # Shape the YC directory needs: Algolia multi-query body built from dotted paths
    tool = AgentTool(name="search", description="d",
                     parameters=[ToolParameter(name="q", required=True), ToolParameter(name="n", type="integer")],
                     endpoint_mappings=[EndpointMapping(
                         method="POST", path="/1/indexes/*/queries",
                         static_params={"requestBody.requests[0].indexName": "Companies", "x-api-key": "stale"},
                         parameter_mappings=[
                             ParameterMapping(tool_param="q", endpoint_param="request_body.requests[0].params.query",
                                              location=ParameterLocation.BODY),
                             ParameterMapping(tool_param="n", endpoint_param="requests[0].params.hitsPerPage",
                                              location=ParameterLocation.BODY)])])
    ex = ToolExecutor("https://abc-dsn.algolia.net", static_query={"x-api-key": "checked"})
    seen = await _capture_request(ex, tool, {"q": "robotics", "n": 2})
    # Algolia rejects "params" as an object, so it is flattened into each request
    assert seen["json"] == {"requests": [{"indexName": "Companies", "query": "robotics", "hitsPerPage": 2}]}
    assert seen["params"] == {"x-api-key": "checked"}


def _secured_key(valid_until: int, scope: str = "restrictIndices=Companies") -> str:
    import base64
    return base64.b64encode(f"{'a' * 64}{scope}&validUntil={valid_until}".encode()).decode()


@pytest.mark.asyncio
async def test_expired_public_key_is_refreshed_from_the_site_page():
    import httpx
    import time
    from machine_internet.serving import executor as ex_mod

    old, fresh = _secured_key(int(time.time()) - 10), _secured_key(int(time.time()) + 86400)
    other = _secured_key(int(time.time()) + 99999, scope="restrictIndices=Other")
    page = f'<script>window.cfg={{"key":"{fresh}","otherKey":"{other}"}}</script>'

    def handler(request):
        return httpx.Response(200, text=page)

    ex = ToolExecutor("https://abc-dsn.algolia.net", static_query={"key": old}, key_source_url="https://site.example/")
    ex_mod._FRESH_KEYS.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await ex._refresh_expiring_keys(client)
    # Picks the key with the same scope, not just the longest-lived one
    assert ex.static_query["key"] == fresh


@pytest.mark.asyncio
async def test_templated_static_body_is_filled_and_missing_pairs_dropped():
    tool = AgentTool(name="search", description="d",
                     parameters=[ToolParameter(name="query"), ToolParameter(name="page", type="integer"),
                                 ToolParameter(name="facets", type="array")],
                     endpoint_mappings=[EndpointMapping(method="POST", path="/1/indexes/*/queries", static_params={
                         "requests": [{"indexName": "Companies",
                                       "params": "query={query}&facets={facets}&page={page}"}]})])
    seen = await _capture_request(ToolExecutor("https://abc-dsn.algolia.net"), tool,
                                  {"query": "ai robots", "facets": ["batch"]})
    assert seen["json"] == {"requests": [{"indexName": "Companies",
                                          "params": "query=ai%20robots&facets=%5B%22batch%22%5D"}]}
    assert not seen["params"]
