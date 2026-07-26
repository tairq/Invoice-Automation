"""Application configuration via pydantic-settings."""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    anthropic = "anthropic"
    openai = "openai"
    custom = "custom"


class StorageBackend(str, Enum):
    local = "local"
    s3 = "s3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_name: str = "InvoiceProcessor"
    debug: bool = True
    secret_key: str = "change-me"

    # Database
    database_url: str = "postgresql+asyncpg://invoice_user:invoice_pass@localhost:5432/invoice_processor"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Storage
    storage_backend: StorageBackend = StorageBackend.local
    storage_path: str = "./storage"
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_s3_bucket: Optional[str] = None
    aws_region: Optional[str] = None

    # LLM
    llm_provider: LLMProvider = LLMProvider.anthropic
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-5-20250601"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    custom_api_key: Optional[str] = None
    custom_api_base: Optional[str] = None
    custom_model: str = "gpt-4o"

    # OCR
    tesseract_cmd: str = "/usr/bin/tesseract"
    ocr_languages: str = "eng"

    # Processing
    confidence_threshold: float = 0.85
    max_file_size_mb: int = 50
    allowed_extensions: str = "pdf,jpg,jpeg,png,tiff,eml"

    # Email
    email_host: Optional[str] = None
    email_port: int = 993
    email_username: Optional[str] = None
    email_password: Optional[str] = None
    email_check_interval: int = 300

    # Airtable
    airtable_api_key: Optional[str] = None
    airtable_base_id: Optional[str] = None
    airtable_invoices_table: str = "Invoices"
    airtable_line_items_table: Optional[str] = "Line Items"
    airtable_sync_enabled: bool = False

    # Logging
    log_level: str = "INFO"

    @property
    def allowed_extensions_list(self) -> list[str]:
        return [ext.strip().lower() for ext in self.allowed_extensions.split(",")]

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def storage_abs_path(self) -> Path:
        p = Path(self.storage_path)
        if not p.is_absolute():
            p = Path(os.getcwd()) / p
        return p.resolve()


settings = Settings()
