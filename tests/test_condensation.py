import json
import pytest
from unittest.mock import AsyncMock, patch

from src.condensation.condenser import condense
from src.discovery.schema import (
    RawSchema,
    Endpoint,
    EndpointParameter,
    ParameterLocation,
    AuthScheme,
)


def _make_raw_schema() -> RawSchema:
    params = [
        EndpointParameter(name="customer_id", location=ParameterLocation.PATH, required=True, type="integer"),
    ]
    return RawSchema(
        base_url="https://api.crm.example.com",
        title="CRM API",
        description="Customer relationship management",
        endpoints=[
            Endpoint(path="/customers", method="GET", summary="List customers", parameters=[]),
            Endpoint(path="/customers/{customer_id}", method="GET", summary="Get customer", parameters=params),
            Endpoint(path="/customers", method="POST", summary="Create customer", parameters=[]),
            Endpoint(path="/customers/{customer_id}", method="PUT", summary="Update customer", parameters=params),
            Endpoint(path="/customers/{customer_id}", method="DELETE", summary="Delete customer", parameters=params),
            Endpoint(path="/deals", method="GET", summary="List deals", parameters=[]),
            Endpoint(path="/deals", method="POST", summary="Create deal", parameters=[]),
        ],
        auth_schemes=[AuthScheme.BEARER],
        discovery_method="openapi",
    )


_MOCK_RESPONSE = {
    "service_name": "crm_api",
    "service_description": "CRM for managing customers and deals.",
    "tools": [
        {
            "name": "list_customers",
            "description": "List all customers.",
            "parameters": [],
            "endpoint_mappings": [{"method": "GET", "path": "/customers", "parameter_mappings": [], "static_params": {}}],
            "response_fields": None,
        },
        {
            "name": "get_customer",
            "description": "Get a customer by ID.",
            "parameters": [{"name": "customer_id", "type": "integer", "required": True, "description": "Customer ID"}],
            "endpoint_mappings": [{"method": "GET", "path": "/customers/{customer_id}", "parameter_mappings": [{"tool_param": "customer_id", "endpoint_param": "customer_id", "location": "path"}], "static_params": {}}],
            "response_fields": None,
        },
        {
            "name": "create_customer",
            "description": "Create a new customer.",
            "parameters": [{"name": "name", "type": "string", "required": True, "description": "Customer name"}],
            "endpoint_mappings": [{"method": "POST", "path": "/customers", "parameter_mappings": [{"tool_param": "name", "endpoint_param": "name", "location": "body"}], "static_params": {}}],
            "response_fields": None,
        },
    ],
}


@pytest.mark.asyncio
async def test_condense_returns_valid_schema():
    raw = _make_raw_schema()

    with patch("src.ai.client.FallbackAIClient.generate", new=AsyncMock(return_value=json.dumps(_MOCK_RESPONSE))):
        condensed = await condense(raw, source_url="https://api.crm.example.com")

    assert condensed.service_name == "crm_api"
    assert len(condensed.tools) == 3
    assert condensed.auth_type == AuthScheme.BEARER
    assert condensed.source_url == "https://api.crm.example.com"


@pytest.mark.asyncio
async def test_condense_tool_parameters():
    raw = _make_raw_schema()

    with patch("src.ai.client.FallbackAIClient.generate", new=AsyncMock(return_value=json.dumps(_MOCK_RESPONSE))):
        condensed = await condense(raw)

    get_customer = next(t for t in condensed.tools if t.name == "get_customer")
    assert len(get_customer.parameters) == 1
    assert get_customer.parameters[0].name == "customer_id"
    assert get_customer.parameters[0].required is True

    mapping = get_customer.endpoint_mappings[0]
    assert mapping.method == "GET"
    assert mapping.path == "/customers/{customer_id}"
    assert mapping.parameter_mappings[0].location == ParameterLocation.PATH
