"""Typed configuration with safe single-host defaults."""

from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from DOCINTEL-prefixed environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DOCINTEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    data_dir: Path = Path(".data")
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8014, ge=1, le=65535)
    ui_port: int = Field(default=8514, ge=1, le=65535)
    api_base_url: str | None = None
    api_token: SecretStr | None = None

    provider_mode: Literal["deterministic", "openai"] = "deterministic"
    embedding_provider: Literal["deterministic", "openai"] = "deterministic"
    openai_api_key: SecretStr | None = None
    openai_chat_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    provider_timeout_seconds: float = Field(default=60.0, ge=1.0, le=300.0)
    provider_max_attempts: int = Field(default=3, ge=1, le=5)

    max_file_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_pages: int = Field(default=250, ge=1, le=2000)
    max_upload_batch: int = Field(default=10, ge=1, le=50)
    max_question_characters: int = Field(default=4000, ge=1, le=20000)
    max_history_turns: int = Field(default=12, ge=0, le=50)
    retrieval_top_k: int = Field(default=10, ge=2, le=50)
    embedding_dimensions: int = Field(default=384, ge=32, le=4096)

    worker_poll_seconds: float = Field(default=0.5, ge=0.05, le=30.0)
    worker_lease_seconds: int = Field(default=120, ge=10, le=3600)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    worker_concurrency: int = Field(default=1, ge=1, le=2)
    enable_ocr: bool = True
    ocr_timeout_seconds: int = Field(default=30, ge=1, le=300)
    page_render_scale: float = Field(default=1.5, ge=0.5, le=4.0)
    demo_mode: bool = True

    @property
    def database_path(self) -> Path:
        return self.data_dir / "document-intelligence.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def resolved_api_base_url(self) -> str:
        return self.api_base_url or f"http://{self.api_host}:{self.api_port}"

    @property
    def is_loopback(self) -> bool:
        if self.api_host.lower() == "localhost":
            return True
        try:
            return ip_address(self.api_host).is_loopback
        except ValueError:
            return False

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> Settings:
        if not self.is_loopback and self.api_token is None:
            raise ValueError("DOCINTEL_API_TOKEN is required for non-loopback API binding")
        if self.provider_mode == "openai" and self.openai_api_key is None:
            raise ValueError("DOCINTEL_OPENAI_API_KEY is required when provider mode is openai")
        if self.embedding_provider == "openai" and self.openai_api_key is None:
            raise ValueError("DOCINTEL_OPENAI_API_KEY is required for OpenAI embeddings")
        return self

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.artifacts_dir, self.chroma_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one validated settings object per process."""

    return Settings()
