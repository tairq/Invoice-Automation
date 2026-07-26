"""Invoice model — the central entity."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.approval_token import ApprovalToken
    from app.models.extracted_data import ExtractedData
    from app.models.extraction_confidence import ExtractionConfidence
    from app.models.line_item import LineItem
    from app.models.organization import Organization
    from app.models.po_match import POMatch
    from app.models.processing_log import ProcessingLog
    from app.models.vendor import Vendor
    from app.models.webhook_delivery import WebhookDelivery


class InvoiceStatus(str, PyEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"
    needs_review = "needs_review"


class ApprovalStatus(str, PyEnum):
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    auto_approved = "auto_approved"


class POMatchStatus(str, PyEnum):
    matched = "matched"
    partial = "partial"
    unmatched = "unmatched"
    discrepancy = "discrepancy"


class PaymentStatus(str, PyEnum):
    unpaid = "unpaid"
    paid = "paid"
    overdue = "overdue"


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
    xero_invoice_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )

    # Approval workflow
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status", create_constraint=True),
        default=ApprovalStatus.pending_approval,
        index=True,
    )

    # PO matching
    matched_po_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("purchase_orders.id"), nullable=True, index=True
    )
    po_match_status: Mapped[Optional[POMatchStatus]] = mapped_column(
        Enum(POMatchStatus, name="po_match_status", create_constraint=True),
        nullable=True,
    )

    # Vendor matching
    vendor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("vendors.id"), nullable=True, index=True
    )
    match_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Payment tracking
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    payment_status: Mapped[Optional[PaymentStatus]] = mapped_column(
        Enum(PaymentStatus, name="payment_status", create_constraint=True),
        default=PaymentStatus.unpaid,
        index=True,
    )

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
    approval_tokens: Mapped[list[ApprovalToken]] = relationship(
        "ApprovalToken", back_populates="invoice", cascade="all, delete-orphan"
    )
    po_matches: Mapped[list[POMatch]] = relationship(
        "POMatch", back_populates="invoice", cascade="all, delete-orphan"
    )
    webhook_deliveries: Mapped[list[WebhookDelivery]] = relationship(
        "WebhookDelivery", back_populates="invoice", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} status={self.status} file={self.original_filename!r}>"
