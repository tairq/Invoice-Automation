"""Invoice model — the central entity."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.extracted_data import ExtractedData
    from app.models.extraction_confidence import ExtractionConfidence
    from app.models.line_item import LineItem
    from app.models.organization import Organization
    from app.models.processing_log import ProcessingLog


class InvoiceStatus(str, PyEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"
    needs_review = "needs_review"


class InvoiceSource(str, PyEnum):
    upload = "upload"
    email = "email"
    folder = "folder"
    api = "api"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=True, index=True
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status", create_constraint=True),
        default=InvoiceStatus.pending,
        index=True,
    )
    source: Mapped[InvoiceSource] = mapped_column(
        Enum(InvoiceSource, name="invoice_source", create_constraint=True),
        default=InvoiceSource.upload,
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    organization: Mapped[Optional[Organization]] = relationship(
        "Organization", back_populates="invoices"
    )
    extracted_data: Mapped[Optional[ExtractedData]] = relationship(
        "ExtractedData", back_populates="invoice", uselist=False, cascade="all, delete-orphan"
    )
    line_items: Mapped[list[LineItem]] = relationship(
        "LineItem", back_populates="invoice", cascade="all, delete-orphan"
    )
    confidence_scores: Mapped[list[ExtractionConfidence]] = relationship(
        "ExtractionConfidence", back_populates="invoice", cascade="all, delete-orphan"
    )
    processing_logs: Mapped[list[ProcessingLog]] = relationship(
        "ProcessingLog", back_populates="invoice", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} status={self.status} file={self.original_filename!r}>"
