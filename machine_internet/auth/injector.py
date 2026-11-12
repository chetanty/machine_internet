from __future__ import annotations
import base64
import time
from typing import Any, Optional

import httpx

from ..discovery.schema import AuthScheme


class AuthInjector:
    def __init__(self, auth_type: AuthScheme, credentials: dict[str, Any]) -> None:
        self.auth_type = auth_type
        self.credentials = credentials
        self._token_cache: Optional[str] = None
        self._token_expiry: float = 0.0

    async def inject(
        self,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> tuple[dict[str, str], dict[str, Any]]:
        headers = dict(headers)
        params = dict(params)

        if self.auth_type == AuthScheme.API_KEY:
            location = self.credentials.get("location", "header")
            name = self.credentials.get("name", "X-API-Key")
            value = self.credentials.get("value", "")
            if location == "header":
                headers[name] = value
            else:
                params[name] = value

        elif self.auth_type == AuthScheme.BEARER:
            headers["Authorization"] = f"Bearer {self.credentials.get('token', '')}"

        elif self.auth_type == AuthScheme.BASIC:
            u = self.credentials.get("username", "")
            p = self.credentials.get("password", "")
            encoded = base64.b64encode(f"{u}:{p}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        elif self.auth_type == AuthScheme.OAUTH2:
            token = await self._oauth2_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        return headers, params

    async def _oauth2_token(self) -> Optional[str]:
        if self._token_cache and time.time() < self._token_expiry - 60:
            return self._token_cache

        token_url = self.credentials.get("token_url", "")
        if not token_url:
            return None

        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data={
                "grant_type": "client_credentials",
                "client_id": self.credentials.get("client_id", ""),
                "client_secret": self.credentials.get("client_secret", ""),
                "scope": self.credentials.get("scope", ""),
            })
            resp.raise_for_status()
            data = resp.json()

        self._token_cache = data.get("access_token")
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        return self._token_cache


def credentials_from_env(auth_type: AuthScheme) -> Optional[dict[str, Any]]:
    """Read credentials for the target API from MI_* environment variables.

    bearer:  MI_TOKEN
    api_key: MI_API_KEY, optional MI_API_KEY_NAME (default X-API-Key), MI_API_KEY_IN (header|query)
    basic:   MI_USERNAME, MI_PASSWORD
    oauth2:  MI_TOKEN_URL, MI_CLIENT_ID, MI_CLIENT_SECRET, optional MI_SCOPE
    """
    import os

    env = os.environ.get
    if auth_type == AuthScheme.BEARER and env("MI_TOKEN"):
        return {"token": env("MI_TOKEN")}
    if auth_type == AuthScheme.API_KEY and env("MI_API_KEY"):
        return {
            "value": env("MI_API_KEY"),
            "name": env("MI_API_KEY_NAME", "X-API-Key"),
            "location": env("MI_API_KEY_IN", "header"),
        }
    if auth_type == AuthScheme.BASIC and env("MI_USERNAME"):
        return {"username": env("MI_USERNAME"), "password": env("MI_PASSWORD", "")}
    if auth_type == AuthScheme.OAUTH2 and env("MI_TOKEN_URL"):
        return {
            "token_url": env("MI_TOKEN_URL"),
            "client_id": env("MI_CLIENT_ID", ""),
            "client_secret": env("MI_CLIENT_SECRET", ""),
            "scope": env("MI_SCOPE", ""),
        }
    return None
