from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The documented `.env` lives at the repository root (see README). Resolve it explicitly so the
# backend picks it up regardless of the working directory (e.g. when run from `backend/`).
REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://heavy_parking:heavy_parking@localhost:5432/heavy_parking"
    redis_url: str = "redis://localhost:6379/0"
    app_version: str = "0.1.0"
    environment: Literal["DEV", "STAGING", "PROD"] = "DEV"
    debug: bool = False
    cursor_signing_key: SecretStr | None = None
    # Server-side Places API (New) key. Unset disables destination search with PLACES_UNAVAILABLE.
    google_places_api_key: SecretStr | None = None
    places_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    places_rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)

    @field_validator("google_places_api_key", mode="before")
    @classmethod
    def blank_places_key_is_unset(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_cursor_key(self):
        if self.cursor_signing_key is not None and len(self.cursor_signing_key.get_secret_value().encode()) < 32:
            raise ValueError("CURSOR_SIGNING_KEY must contain at least 32 bytes")
        if self.environment == "PROD" and self.cursor_signing_key is None:
            raise ValueError("PROD requires a shared CURSOR_SIGNING_KEY")
        return self

    model_config = SettingsConfigDict(env_file=REPO_ROOT_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


settings = Settings()
