"""Export service — generate CSV and JSON output from extracted data."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extracted_data import ExtractedData
from app.models.invoice import Invoice
from app.models.line_item import LineItem


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        return super().default(obj)


async def export_invoices_csv(
    db: AsyncSession,
    invoice_ids: Optional[list[uuid.UUID]] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> str:
    """Export invoice data as CSV string."""
    invoices = await _query_invoices(db, invoice_ids, date_from, date_to)

    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "Invoice ID",
        "Status",
        "Invoice Number",
        "Invoice Type",
        "Issue Date",
        "Due Date",
        "Currency",
        "Vendor Name",
        "Vendor Tax ID",
        "Vendor Email",
        "Customer Name",
        "Customer Tax ID",
        "PO Number",
        "Subtotal",
        "Tax Total",
        "Discount",
        "Grand Total",
        "Amount Due",
        "Payment Terms",
        "Confidence",
        "Line Items Count",
        "Processed At",
    ]
    writer.writerow(headers)

    for invoice in invoices:
        ed = invoice.extracted_data
        line_count = len(invoice.line_items) if invoice.line_items else 0

        writer.writerow(
            [
                str(invoice.id),
                invoice.status.value if hasattr(invoice.status, "value") else invoice.status,
                ed.invoice_number if ed else "",
                ed.invoice_type if ed else "",
                ed.issue_date.isoformat() if ed and ed.issue_date else "",
                ed.due_date.isoformat() if ed and ed.due_date else "",
                ed.currency if ed else "",
                ed.vendor_name if ed else "",
                ed.vendor_tax_id if ed else "",
                ed.vendor_email if ed else "",
                ed.customer_name if ed else "",
                ed.customer_tax_id if ed else "",
                ed.po_number if ed else "",
                ed.subtotal if ed else "",
                ed.tax_total if ed else "",
                ed.discount_total if ed else "",
                ed.grand_total if ed else "",
                ed.amount_due if ed else "",
                ed.payment_terms if ed else "",
                f"{invoice.confidence_score:.2f}" if invoice.confidence_score else "",
                str(line_count),
                invoice.processed_at.isoformat() if invoice.processed_at else "",
            ]
        )

    return output.getvalue()


async def export_invoices_json(
    db: AsyncSession,
    invoice_ids: Optional[list[uuid.UUID]] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> str:
    """Export invoice data as JSON string."""
    invoices = await _query_invoices(db, invoice_ids, date_from, date_to)

    result = []
    for invoice in invoices:
        ed = invoice.extracted_data
        result.append(
            {
                "id": str(invoice.id),
                "status": invoice.status.value
                if hasattr(invoice.status, "value")
                else invoice.status,
                "filename": invoice.original_filename,
                "file_type": invoice.file_type,
                "confidence_score": invoice.confidence_score,
                "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
                "processed_at": invoice.processed_at.isoformat() if invoice.processed_at else None,
                "extracted_data": {
                    "invoice_number": ed.invoice_number if ed else None,
                    "invoice_type": ed.invoice_type if ed else None,
                    "issue_date": ed.issue_date.isoformat() if ed and ed.issue_date else None,
                    "due_date": ed.due_date.isoformat() if ed and ed.due_date else None,
                    "currency": ed.currency if ed else None,
                    "vendor_name": ed.vendor_name if ed else None,
                    "vendor_tax_id": ed.vendor_tax_id if ed else None,
                    "customer_name": ed.customer_name if ed else None,
                    "po_number": ed.po_number if ed else None,
                    "subtotal": float(ed.subtotal) if ed and ed.subtotal else None,
                    "tax_total": float(ed.tax_total) if ed and ed.tax_total else None,
                    "grand_total": float(ed.grand_total) if ed and ed.grand_total else None,
                    "amount_due": float(ed.amount_due) if ed and ed.amount_due else None,
                    "line_items": [
                        {
                            "line_number": li.line_number,
                            "description": li.description,
                            "quantity": float(li.quantity) if li.quantity else None,
                            "unit_price": float(li.unit_price) if li.unit_price else None,
                            "net_amount": float(li.net_amount) if li.net_amount else None,
                            "tax_rate": float(li.tax_rate) if li.tax_rate else None,
                        }
                        for li in (invoice.line_items or [])
                    ]
                    if invoice.line_items
                    else [],
                }
                if ed
                else None,
            }
        )

    return json.dumps(result, cls=DecimalEncoder, indent=2)


async def _query_invoices(
    db: AsyncSession,
    invoice_ids: Optional[list[uuid.UUID]] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[Invoice]:
    """Query invoices with optional filters."""
    query = (
        select(Invoice)
        .outerjoin(ExtractedData)
        .outerjoin(LineItem)
        .distinct()
        .order_by(Invoice.created_at.desc())
    )

    if invoice_ids:
        query = query.where(Invoice.id.in_(invoice_ids))
    if date_from:
        query = query.where(Invoice.created_at >= date_from)
    if date_to:
        query = query.where(Invoice.created_at <= date_to)

    result = await db.execute(query)
    invoices = result.unique().scalars().all()

    # Eagerly load relationships
    for inv in invoices:
        if inv.extracted_data:
            _ = inv.extracted_data.id
        if inv.line_items:
            _ = [li.id for li in inv.line_items]

    return invoices
