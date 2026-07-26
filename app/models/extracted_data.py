"""Structured extraction output for an invoice."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice


class ExtractedData(Base):
    __tablename__ = "extracted_data"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("invoices.id"), unique=True, nullable=False
    )
    invoice_number: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    invoice_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    issue_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    period_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    payment_terms: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Vendor
    vendor_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    vendor_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    vendor_tax_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    vendor_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vendor_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    vendor_bank_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vendor_bank_account: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    vendor_bank_iban: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    vendor_bank_swift: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Vendor verification
    vendor_verified: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    vendor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("vendors.id"), nullable=True, index=True
    )

    # Customer
    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customer_tax_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Totals
    subtotal: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    tax_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    discount_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    grand_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True, index=True)
    amount_due: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    amount_paid: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)

    # Metadata
    raw_extraction: Mapped[Optional[dict]] = mapped_column(
        "raw_extraction_json", Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationship
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="extracted_data")

    def __repr__(self) -> str:
        return f"<ExtractedData invoice={self.invoice_id} number={self.invoice_number!r}>"
