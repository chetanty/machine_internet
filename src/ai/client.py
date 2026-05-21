"""Multi-provider AI client with automatic fallback on quota and server errors.

Fallback order (configured via .env):

  1. Primary Gemini key + primary model  (GEMINI_API_KEY + GEMINI_MODEL)
  2. Primary Gemini key + fallback models (GEMINI_FALLBACK_MODELS=model-a,model-b)
     Each model has its own independent daily quota pool.
  3. Secondary Gemini keys  (GEMINI_API_KEY_2 … GEMINI_API_KEY_5)
  4. OpenAI  (OPENAI_API_KEY + OPENAI_MODEL, default gpt-4o-mini)

When a call raises a quota (429) or server (503) error the client logs the
failure and immediately retries with the next slot. Non-quota errors raise
immediately without falling back.
"""
from __future__ import annotations
from typing import Optional

from google import genai
from google.genai import types as genai_types

from ..config import settings


_RETRYABLE = (
    "429", "quota", "rate_limit", "rate limit",
    "resource_exhausted", "resource exhausted",
    "503", "unavailable", "overloaded",
)


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in _RETRYABLE)


def _build_agent_list() -> list[dict]:
    primary_key = settings.gemini_api_key
    primary_model = settings.gemini_model
    agents: list[dict] = []

    # 1. OpenAI (primary)
    if settings.openai_api_key:
        agents.append({"provider": "openai", "api_key": settings.openai_api_key, "model": settings.openai_model})

    # 2. Primary Gemini (fallback)
    if primary_key:
        agents.append({"provider": "gemini", "api_key": primary_key, "model": primary_model})

    # 3. Same Gemini key, fallback models
    if primary_key and settings.gemini_fallback_models:
        for model in settings.gemini_fallback_models.split(","):
            model = model.strip()
            if model:
                agents.append({"provider": "gemini", "api_key": primary_key, "model": model})

    # 4. Secondary Gemini keys
    for key, model in [
        (settings.gemini_api_key_2, settings.gemini_model_2 or primary_model),
        (settings.gemini_api_key_3, settings.gemini_model_3 or primary_model),
        (settings.gemini_api_key_4, settings.gemini_model_4 or primary_model),
        (settings.gemini_api_key_5, settings.gemini_model_5 or primary_model),
    ]:
        if key:
            agents.append({"provider": "gemini", "api_key": key, "model": model})

    return agents


async def _call_gemini(prompt: str, system: Optional[str], max_tokens: int, agent: dict) -> str:
    client = genai.Client(api_key=agent["api_key"])
    cfg_kwargs: dict = {"max_output_tokens": max_tokens}
    if system:
        cfg_kwargs["system_instruction"] = system
    response = await client.aio.models.generate_content(
        model=agent["model"],
        contents=prompt,
        config=genai_types.GenerateContentConfig(**cfg_kwargs),
    )
    return response.text.strip()


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


_CALLERS = {
    "gemini": _call_gemini,
    "openai": _call_openai,
}


class FallbackAIClient:
    """Calls Gemini (with model fallback) then OpenAI on quota/server errors."""

    def __init__(self) -> None:
        self._agents = _build_agent_list()
        if not self._agents:
            raise RuntimeError("No AI API keys configured — set GEMINI_API_KEY or OPENAI_API_KEY in .env")

    async def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: int = 32768,
    ) -> str:
        """Generate content, falling back through providers on quota/server errors."""
        last_exc: Optional[Exception] = None

        for i, agent in enumerate(self._agents):
            caller = _CALLERS[agent["provider"]]
            try:
                result = await caller(prompt, system, max_tokens, agent)
                if i > 0:
                    print(f"  [ai] using fallback agent {i + 1} ({agent['provider']}:{agent['model']})")
                return result

            except Exception as exc:
                if _is_retryable(exc):
                    last_exc = exc
                    print(f"  [ai] agent {i + 1} ({agent['provider']}:{agent['model']}) quota/error — trying next: {exc}")
                    continue
                raise

        raise RuntimeError(
            f"All {len(self._agents)} AI agent(s) exhausted — no quota remaining."
        ) from last_exc

    @property
    def agent_count(self) -> int:
        return len(self._agents)

    @property
    def agent_summary(self) -> list[str]:
        return [f"agent{i + 1}:{a['provider']}:{a['model']}" for i, a in enumerate(self._agents)]


_client: Optional[FallbackAIClient] = None


def get_gemini_client() -> FallbackAIClient:
    """Return the module-level singleton, constructing it on first call."""
    global _client
    if _client is None:
        _client = FallbackAIClient()
    return _client
