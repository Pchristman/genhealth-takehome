from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # The root .env serves local dev and Docker Compose alike.
    # hide_input_in_errors keeps a bad secret out of the startup error.
    # env_ignore_empty makes a blank variable count as unset, so JWT_SECRET= fails as missing.
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    jwt_secret: str = Field(min_length=32)
    jwt_ttl_minutes: int = Field(default=60, gt=0)
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

    @field_validator("jwt_secret")
    @classmethod
    def reject_worthless_secret(cls, value: str) -> str:
        # Length alone passes 32 spaces. The message must never quote the value.
        if len(set(value.strip())) < 10:
            raise ValueError("JWT_SECRET needs at least 10 distinct characters.")
        return value

    @field_validator("log_level")
    @classmethod
    def upper_log_level(cls, value: str) -> str:
        return value.upper()


settings = Settings()
