from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_ENVIRONMENTS = {"development", "local", "test", "dev"}

_WEAK_SECRET_KEYS = {
    "dev-secret-key-change-in-production-32chars",
    "secret",
    "changeme",
    "",
}

_WEAK_WEBHOOK_SECRETS = {
    "changeme",
    "secret",
    "webhook_secret",
    "",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str = "postgresql+asyncpg://nexplane:nexplane_dev@localhost:5432/nexplane"
    SECRET_KEY: str = "dev-secret-key-change-in-production-32chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    ENVIRONMENT: str = "development"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    AI_MODEL: str = "claude-sonnet-4-6"
    WEBHOOK_SECRET: str = "changeme"

    @model_validator(mode="after")
    def reject_weak_secrets_in_production(self) -> "Settings":
        if self.ENVIRONMENT.lower() in _DEV_ENVIRONMENTS:
            return self
        if self.SECRET_KEY in _WEAK_SECRET_KEYS or len(self.SECRET_KEY) < 32:
            raise ValueError(
                f"SECRET_KEY is a development default or too short for ENVIRONMENT={self.ENVIRONMENT!r}. "
                "Set a strong SECRET_KEY (>=32 chars) in your environment."
            )
        if self.WEBHOOK_SECRET in _WEAK_WEBHOOK_SECRETS or len(self.WEBHOOK_SECRET) < 16:
            raise ValueError(
                f"WEBHOOK_SECRET is a development default for ENVIRONMENT={self.ENVIRONMENT!r}. "
                "Set a strong WEBHOOK_SECRET in your environment."
            )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


settings = Settings()
