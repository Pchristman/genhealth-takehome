from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # The root .env serves local dev and Docker Compose alike.
    # hide_input_in_errors keeps a bad secret out of the startup error.
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    jwt_secret: str = Field(min_length=32)
    jwt_ttl_minutes: int = 60
    database_url: str = "sqlite:///./app.db"
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_max_attempts: int = 3
    llm_timeout_seconds: float = 120.0
    upload_max_bytes: int = 20_000_000
    upload_max_pages: int = 100
    quota_user_daily: int = 20
    quota_global_daily: int = 100
    log_level: str = "INFO"


settings = Settings()
