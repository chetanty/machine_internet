from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """LLM provider settings, read from the environment or a local .env file.

    Set any one key. When several are set they are tried in this order,
    falling through to the next on quota or server errors:
    Anthropic, OpenAI, Gemini.
    """

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Comma-separated extra Gemini models tried on the same key when the primary hits its quota
    # Free tier quotas are per model and per day, so a second model keeps wrap working when one runs out
    gemini_fallback_models: str = "gemini-2.5-flash-lite"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
