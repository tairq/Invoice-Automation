"""PO Match model — stores the result of purchase order matching."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.purchase_order import PurchaseOrder


class POMatch(Base):
    __tablename__ = "po_matches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("invoices.id"), nullable=False, index=True
    )
    po_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("purchase_orders.id"), nullable=False, index=True
    )
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    discrepancies: Mapped[Optional[dict]] = mapped_column(Text, nullable=True)  # JSON string
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="po_matches")
    purchase_order: Mapped[PurchaseOrder] = relationship("PurchaseOrder")

    def __repr__(self) -> str:
        return (
            f"<POMatch invoice={self.invoice_id} po={self.po_id} conf={self.match_confidence:.2f}>"
        )
