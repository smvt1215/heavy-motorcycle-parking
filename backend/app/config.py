from pathlib import Path
from typing import Literal

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

    model_config = SettingsConfigDict(env_file=REPO_ROOT_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


settings = Settings()
