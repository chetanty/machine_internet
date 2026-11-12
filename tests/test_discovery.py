import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from machine_internet.discovery.openapi import discover_openapi, _parse_spec
from machine_internet.discovery.schema import RawSchema, AuthScheme


PETSTORE_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Pet Store", "version": "1.0.0", "description": "A sample API"},
    "servers": [{"url": "https://petstore.example.com"}],
    "paths": {
        "/pets": {
            "get": {
                "summary": "List pets",
                "operationId": "listPets",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}},
                ],
                "responses": {"200": {"description": "ok"}},
            },
            "post": {
                "summary": "Create pet",
                "operationId": "createPet",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "tag": {"type": "string"},
                                },
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "created"}},
            },
        },
        "/pets/{id}": {
            "get": {
                "summary": "Get pet by id",
                "operationId": "getPet",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}},
                ],
                "responses": {"200": {"description": "ok"}},
            },
        },
    },
    "components": {
        "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
        }
    },
}


def test_parse_spec_basic():
    schema = _parse_spec(PETSTORE_SPEC, "https://petstore.example.com")
    assert schema.title == "Pet Store"
    assert schema.base_url == "https://petstore.example.com"
    assert len(schema.endpoints) == 3
    assert AuthScheme.BEARER in schema.auth_schemes
    assert schema.discovery_method == "openapi"


def test_parse_spec_parameters():
    schema = _parse_spec(PETSTORE_SPEC, "https://petstore.example.com")
    list_ep = next(e for e in schema.endpoints if e.operation_id == "listPets")
    assert any(p.name == "limit" for p in list_ep.parameters)

    get_ep = next(e for e in schema.endpoints if e.operation_id == "getPet")
    id_param = next(p for p in get_ep.parameters if p.name == "id")
    assert id_param.required is True
    assert id_param.location.value == "path"


def test_parse_spec_request_body():
    schema = _parse_spec(PETSTORE_SPEC, "https://petstore.example.com")
    create_ep = next(e for e in schema.endpoints if e.operation_id == "createPet")
    param_names = [p.name for p in create_ep.parameters]
    assert "name" in param_names
    name_param = next(p for p in create_ep.parameters if p.name == "name")
    assert name_param.required is True


@pytest.mark.asyncio
async def test_discover_openapi_success():
    with patch("machine_internet.discovery.openapi.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = PETSTORE_SPEC

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        schema = await discover_openapi("https://petstore.example.com")

    assert schema is not None
    assert schema.title == "Pet Store"


@pytest.mark.asyncio
async def test_discover_openapi_returns_none_when_no_spec():
    with patch("machine_internet.discovery.openapi.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        schema = await discover_openapi("https://no-spec.example.com")

    assert schema is None


@pytest.mark.asyncio
async def test_discover_openapi_skips_spa_html_shell():
    """SPAs serve index.html for every path; that must read as 'no spec', not a parse error."""
    with patch("machine_internet.discovery.openapi.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.text = "<!doctype html><html><body><div id=root></div></body></html>"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        schema = await discover_openapi("https://spa.example.com")

    assert schema is None


def test_public_keys_replays_constant_keys_but_never_secrets():
    from machine_internet.discovery.traffic import _TrafficEntry, _public_keys

    def entry(q):
        return _TrafficEntry("POST", f"https://abc-dsn.algolia.net/1/indexes/x/query?{q}", None, "{}", 200,
                             {"x-algolia-api-key": "pub123", "x-algolia-application-id": "ABC",
                              "cookie": "s=1", "x-csrf-token": "t", "x-request-id": q})

    headers, query = _public_keys([entry("a=1"), entry("a=2")], "https://abc-dsn.algolia.net")
    assert headers == {"x-algolia-api-key": "pub123", "x-algolia-application-id": "ABC"}
    assert "cookie" not in headers and "x-csrf-token" not in headers
    assert "x-request-id" not in headers  # differs between requests
    assert query == {}
