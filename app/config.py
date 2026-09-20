"""Settings, read from the environment."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://pennylane:pennylane@localhost:5432/pennylane"
    quota_conversations_per_hour: int = 5
    quota_messages_per_hour: int = 30


settings = Settings()
