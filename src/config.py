from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Primary Gemini agent
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Comma-separated fallback models tried on the SAME key when primary model quota is hit
    # e.g. GEMINI_FALLBACK_MODELS=gemini-2.0-flash-lite,gemini-1.5-flash
    gemini_fallback_models: str = ""
    # Additional API keys (separate accounts) tried after all model fallbacks are exhausted
    gemini_api_key_2: str = ""
    gemini_model_2: str = ""
    gemini_api_key_3: str = ""
    gemini_model_3: str = ""
    gemini_api_key_4: str = ""
    gemini_model_4: str = ""
    gemini_api_key_5: str = ""
    gemini_model_5: str = ""

    # OpenAI fallback — tried after all Gemini agents are exhausted
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    database_url: str = "postgresql://postgres:postgres@localhost:5432/uaa"
    api_port: int = 8001

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
