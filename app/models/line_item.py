"""Line items extracted from an invoice."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("invoices.id"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    tax_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    tax_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    discount_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    net_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    gross_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    item_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationship
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="line_items")

    def __repr__(self) -> str:
        return f"<LineItem #{self.line_number} invoice={self.invoice_id}>"
