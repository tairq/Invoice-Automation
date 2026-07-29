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
    debug: bool = False
    secret_key: str = ""

    def validate_security(self) -> None:
        """Reject placeholder security settings outside an explicit debug environment."""
        if self.debug:
            return
        if not self.secret_key or self.secret_key in {"change-me", "change-me-to-a-random-string"}:
            raise ValueError("SECRET_KEY must be set to a strong random value when DEBUG=false")
        if not self.admin_api_key or self.admin_api_key == "change-me-admin-key":
            raise ValueError("ADMIN_API_KEY must be set when DEBUG=false")

    # Database
    database_url: str = (
        "postgresql+asyncpg://invoice_user:invoice_pass@localhost:5432/invoice_processor"
    )

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

    # Admin
    admin_api_key: str = "change-me-admin-key"

    # Xero
    xero_client_id: Optional[str] = None
    xero_client_secret: Optional[str] = None
    xero_enabled: bool = False
    xero_redirect_uri: str = "http://localhost:18080/xero-callback"

    # Approval Workflow
    approval_threshold: float = 0.0  # 0 = require approval for all invoices
    approval_base_url: str = "http://localhost:8000"
    approval_from_email: str = "noreply@invoiceprocessor.com"
    approval_recipient_email: str = "admin@example.com"

    # SMTP
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    smtp_tls: bool = True

    # Payment Reminders
    payment_reminder_email: str = "accounts@example.com"

    # Webhook / Admin
    admin_email: str = "admin@example.com"

    # n8n Integration
    n8n_webhook_url: Optional[str] = None
    n8n_enabled: bool = False

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
