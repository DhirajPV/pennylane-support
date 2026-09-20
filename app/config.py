"""Settings, read from the environment."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://pennylane:pennylane@localhost:5432/pennylane"
    database_url_test: str = "postgresql+psycopg://pennylane:pennylane@localhost:5432/pennylane_test"


settings = Settings()
