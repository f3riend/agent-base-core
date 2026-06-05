"""Ortam değişkenleri (.env); JWT ve PostgreSQL bağlantısı."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    SECRET_KEY: str = "dev-insecure-change-with-SECRET_KEY-in-env"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    ALGORITHM: str = "HS256"
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/agentbase"


env_settings = EnvSettings()
