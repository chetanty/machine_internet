from __future__ import annotations
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class AuthScheme(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    OAUTH2 = "oauth2"
    BASIC = "basic"


class ParameterLocation(str, Enum):
    QUERY = "query"
    HEADER = "header"
    PATH = "path"
    BODY = "body"
    COOKIE = "cookie"


class EndpointParameter(BaseModel):
    name: str
    location: ParameterLocation
    required: bool = False
    type: str = "string"
    description: str = ""
    enum: Optional[list[str]] = None
    default: Optional[Any] = None


class Endpoint(BaseModel):
    path: str
    method: str
    operation_id: Optional[str] = None
    summary: str = ""
    description: str = ""
    parameters: list[EndpointParameter] = Field(default_factory=list)
    request_body_schema: Optional[dict[str, Any]] = None
    response_schema: Optional[dict[str, Any]] = None
    tags: list[str] = Field(default_factory=list)


class RawSchema(BaseModel):
    base_url: str
    title: str = "Unknown Service"
    description: str = ""
    version: str = "1.0.0"
    endpoints: list[Endpoint] = Field(default_factory=list)
    auth_schemes: list[AuthScheme] = Field(default_factory=list)
    discovery_method: str = "unknown"


class ParameterMapping(BaseModel):
    tool_param: str
    endpoint_param: str
    location: ParameterLocation
    transform: Optional[str] = None


class EndpointMapping(BaseModel):
    method: str
    path: str
    parameter_mappings: list[ParameterMapping] = Field(default_factory=list)
    static_params: dict[str, Any] = Field(default_factory=dict)


class ToolParameter(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    enum: Optional[list[str]] = None


class AgentTool(BaseModel):
    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)
    endpoint_mappings: list[EndpointMapping] = Field(default_factory=list)
    response_fields: Optional[list[str]] = None


class CondensedSchema(BaseModel):
    base_url: str
    service_name: str
    service_description: str = ""
    tools: list[AgentTool] = Field(default_factory=list)
    auth_type: AuthScheme = AuthScheme.NONE
    auth_config: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    source_url: str = ""
