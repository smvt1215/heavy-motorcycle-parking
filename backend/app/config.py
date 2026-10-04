from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://heavy_parking:heavy_parking@localhost:5432/heavy_parking"
    redis_url: str = "redis://localhost:6379/0"
    app_version: str = "0.1.0"
    environment: Literal["DEV", "STAGING", "PROD"] = "DEV"
    debug: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
