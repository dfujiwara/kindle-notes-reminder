"""
Application configuration management using Pydantic Settings.

This module provides centralized configuration for the entire application,
loading values from environment variables and .env files with validation.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file.

    Required Settings:
        openai_api_key: OpenAI API key for LLM and embedding generation

    Optional Settings (with defaults):
        database_url: PostgreSQL connection string
        db_echo: Whether to echo SQL queries (useful for debugging)
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        cors_allow_origin: Production CORS origin (if None, uses dev defaults)

    OpenAI Model Configuration:
        openai_llm_model: Model for context generation (default: gpt-4o-mini)
        openai_embedding_model: Model for embeddings (default: text-embedding-3-small)
        embedding_dimension: Vector dimension for embeddings (default: 1536)
        default_evaluation_model: Model name stored in evaluations (default: gpt-4)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # API key - optional for testing, required for production use
    # Runtime validation happens in OpenAI client to allow test imports
    openai_api_key: SecretStr | None = None

    # Optional settings with sensible defaults
    database_url: str = "postgresql://postgres:postgres@localhost:5432/fastapi_db"
    db_echo: bool = False
    log_level: str = "INFO"
    cors_allow_origin: str | None = None

    # OpenAI Model Configuration
    openai_llm_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    default_evaluation_model: str = "gpt-4"

    # Upload limits
    max_upload_size: int = 10_000_000  # 10MB file upload limit

    # URL ingestion configuration
    max_url_content_size: int = 500_000  # 500KB HTML limit
    url_fetch_timeout: int = 30  # seconds

    # Twitter ingestion configuration
    twitter_bearer_token: SecretStr | None = None
    twitter_fetch_timeout: int = 30  # seconds
    max_thread_depth: int = 50
    twitter_rate_limit_retries: int = 3


# Create global settings instance
# This will validate and fail-fast on import if required config is missing
settings = Settings()  # type: ignore[call-arg]
