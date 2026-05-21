import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.discovery.schema import (
    AgentTool,
    CondensedSchema,
    EndpointMapping,
    ParameterLocation,
    ParameterMapping,
    ToolParameter,
    AuthScheme,
)
from src.serving.executor import ToolExecutor


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

    with patch("src.serving.executor.httpx.AsyncClient") as mock_cls:
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
