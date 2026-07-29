"""Per-field confidence tracking for extraction results."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice


class ExtractionMethod(str):
    llm = "llm"
    ocr = "ocr"
    regex = "regex"
    rule = "rule"


class ExtractionConfidence(Base):
    __tablename__ = "extraction_confidence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("invoices.id"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    method: Mapped[str] = mapped_column(String(20), nullable=False, default="llm")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    corrected_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewer_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="confidence_scores")

    def __repr__(self) -> str:
        return (
            f"<ExtractionConfidence field={self.field_name!r}"
            f" conf={self.confidence:.2f} method={self.method}>"
        )
