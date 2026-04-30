from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://jobmatch:dev@localhost:5432/jobmatch"
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""

    # Storage
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_resumes: str = ""

    # Email
    resend_api_key: str = ""
    resend_from_email: str = ""

    # Auth
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_jwt_public_key: str = ""

    # Observability
    sentry_dsn: str = ""
    logfire_token: str = ""

    # App
    app_env: str = "dev"
    app_log_level: str = "INFO"
    pipeline_cost_cap_usd: float = Field(default=1.50)
    pipeline_time_cap_s: int = Field(default=45)
    tenant_monthly_cost_cap_usd: float = Field(default=50.0)

    # Encryption
    db_encryption_key: str = ""


settings = Settings()
