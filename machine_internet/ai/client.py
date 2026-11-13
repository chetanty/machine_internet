"""Multi-provider LLM client with automatic fallback on quota and server errors.

Uses whichever API key is configured (see config.py). When more than one is
set, a quota (429), auth (401/403) or server (5xx) error moves on to the next provider.
Any other error raises immediately.
"""
from __future__ import annotations

import sys
from typing import Optional

from ..config import settings

_RETRYABLE = (
    "429", "quota", "rate_limit", "rate limit",
    "resource_exhausted", "resource exhausted",
    "500", "502", "503", "529", "unavailable", "overloaded",
    # A bad or revoked key on one provider should not block the others
    "401", "403", "invalid_api_key", "invalid api key", "authentication", "permission",
)


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in _RETRYABLE)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _build_agent_list() -> list[dict]:
    agents: list[dict] = []
    if settings.anthropic_api_key:
        agents.append({"provider": "anthropic", "api_key": settings.anthropic_api_key, "model": settings.anthropic_model})
    if settings.openai_api_key:
        agents.append({"provider": "openai", "api_key": settings.openai_api_key, "model": settings.openai_model})
    if settings.gemini_api_key:
        agents.append({"provider": "gemini", "api_key": settings.gemini_api_key, "model": settings.gemini_model})
        for model in settings.gemini_fallback_models.split(","):
            if model.strip():
                agents.append({"provider": "gemini", "api_key": settings.gemini_api_key, "model": model.strip()})
    return agents


async def _call_anthropic(prompt: str, system: Optional[str], max_tokens: int, agent: dict) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=agent["api_key"])
    kwargs: dict = {
        "model": agent["model"],
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        # Re-run on a fallback model if the primary model declines the request
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }
    if system:
        kwargs["system"] = system
    # Streamed so large max_tokens values do not hit HTTP timeouts
    async with client.beta.messages.stream(**kwargs) as stream:
        message = await stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("Anthropic model declined the request")
    return "".join(b.text for b in message.content if b.type == "text").strip()


async def _call_openai(prompt: str, system: Optional[str], max_tokens: int, agent: dict) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=agent["api_key"])
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = await client.chat.completions.create(
        model=agent["model"],
        messages=messages,
        max_completion_tokens=min(max_tokens, 16384),
    )
    return response.choices[0].message.content.strip()


async def _call_gemini(prompt: str, system: Optional[str], max_tokens: int, agent: dict) -> str:
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=agent["api_key"])
    cfg_kwargs: dict = {"max_output_tokens": max_tokens}
    if system:
        cfg_kwargs["system_instruction"] = system
    response = await client.aio.models.generate_content(
        model=agent["model"],
        contents=prompt,
        config=genai_types.GenerateContentConfig(**cfg_kwargs),
    )
    if not response.text:
        # Happens when thinking uses the whole budget or a safety filter blocks the output
        raise RuntimeError("Gemini returned an empty response (unavailable), trying next")
    return response.text.strip()


_CALLERS = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
}


class NoAPIKeyError(RuntimeError):
    pass


class FallbackAIClient:
    """Calls each configured provider in turn until one succeeds."""

    def __init__(self) -> None:
        self._agents = _build_agent_list()
        if not self._agents:
            raise NoAPIKeyError(
                "No LLM API key found. Set one of ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY."
            )

    async def generate(self, prompt: str, *, system: Optional[str] = None, max_tokens: int = 32768) -> str:
        last_exc: Optional[Exception] = None
        for i, agent in enumerate(self._agents):
            try:
                result = await _CALLERS[agent["provider"]](prompt, system, max_tokens, agent)
                if i > 0:
                    _log(f"  [ai] used fallback {agent['provider']}:{agent['model']}")
                return result
            except Exception as exc:
                # A missing SDK package for one provider should not stop the others from being tried
                if _is_retryable(exc) or isinstance(exc, ImportError):
                    last_exc = exc
                    _log(f"  [ai] {agent['provider']}:{agent['model']} failed ({str(exc)[:120]}), trying next")
                    continue
                raise
        raise RuntimeError(f"All {len(self._agents)} LLM provider(s) exhausted (quota or server errors).") from last_exc


_client: Optional[FallbackAIClient] = None


def get_ai_client() -> FallbackAIClient:
    """Return the module-level singleton, constructing it on first call."""
    global _client
    if _client is None:
        _client = FallbackAIClient()
    return _client
