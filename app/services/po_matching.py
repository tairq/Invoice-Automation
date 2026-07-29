"""PO / 3-way matching service — match invoices against purchase orders."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice, POMatchStatus

logger = logging.getLogger(__name__)

VENDOR_MATCH_THRESHOLD = 85  # percent
LINE_ITEM_VARIANCE_THRESHOLD = 0.05  # 5%


async def match_purchase_order(invoice_id: str, db: AsyncSession | None = None) -> dict:
    """Find and match a purchase order for the given invoice.

    Returns a dict with match result information.
    """
    from app.models.invoice import InvoiceStatus
    from app.models.po_match import POMatch
    from app.models.processing_log import ProcessingLog
    from app.models.purchase_order import PurchaseOrder

    close_session = False
    if db is None:
        from app.database import async_session_factory

        db = async_session_factory()
        close_session = True

    try:
        # Convert string ID to UUID (required for SQLite compatibility)
        inv_uuid = uuid.UUID(invoice_id)

        # Load invoice with extracted data and line items
        result = await db.execute(
            select(Invoice)
            .options(
                selectinload(Invoice.extracted_data),
                selectinload(Invoice.line_items),
            )
            .where(Invoice.id == inv_uuid)
        )
        invoice = result.scalar_one_or_none()
        if not invoice:
            logger.warning("Invoice %s not found for PO matching", invoice_id)
            return {"matched": False, "reason": "Invoice not found"}

        ed = invoice.extracted_data
        if not ed:
            logger.info("Invoice %s has no extracted data — skipping PO match", invoice_id)
            return {"matched": False, "reason": "No extracted data"}

        # Load all open POs
        po_result = await db.execute(select(PurchaseOrder).where(PurchaseOrder.status == "open"))
        all_pos = po_result.scalars().all()

        if not all_pos:
            logger.info("No open purchase orders to match against")
            invoice.po_match_status = POMatchStatus.unmatched
            await db.flush()
            return {"matched": False, "reason": "No POs available"}

        # Build search text from extracted data
        search_text = " ".join(
            str(v)
            for v in [
                ed.invoice_number,
                ed.po_number,
                ed.notes,
                ed.vendor_name,
            ]
            if v
        ).lower()

        best_match = None
        best_score = 0.0

        for po in all_pos:
            # 1. Direct PO number match
            if ed.po_number and ed.po_number.lower() == po.po_number.lower():
                score = 100.0
            elif ed.invoice_number and ed.invoice_number.lower() == po.po_number.lower():
                score = 100.0
            elif po.po_number.lower() in search_text:
                score = 95.0
            else:
                # 2. Fuzzy vendor name match
                if ed.vendor_name:
                    vendor_score = fuzz.token_sort_ratio(
                        ed.vendor_name.lower(), po.vendor_name.lower()
                    )
                    if vendor_score >= VENDOR_MATCH_THRESHOLD:
                        score = float(vendor_score) * 0.9  # Slightly lower confidence for fuzzy
                    else:
                        continue
                else:
                    continue

            if score > best_score:
                best_score = score
                best_match = po

        if not best_match:
            invoice.po_match_status = POMatchStatus.unmatched
            await db.flush()
            logger.info("Invoice %s: no PO matched", invoice_id)
            return {"matched": False, "reason": "No matching PO found"}

        # Compare line items for discrepancies
        discrepancies = _compare_line_items(
            invoice.line_items or [],
            best_match.line_items,
        )

        # Determine match status
        if discrepancies:
            match_status = POMatchStatus.discrepancy
            invoice.status = InvoiceStatus.needs_review
            invoice.needs_review = True
        else:
            match_status = POMatchStatus.matched

        # Create POMatch record
        po_match = POMatch(
            invoice_id=invoice.id,
            po_id=best_match.id,
            match_confidence=best_score / 100.0,
            discrepancies=json.dumps(discrepancies) if discrepancies else None,
        )
        db.add(po_match)

        # Update invoice
        invoice.matched_po_id = best_match.id
        invoice.po_match_status = match_status

        # Log
        log = ProcessingLog(
            invoice_id=invoice.id,
            step="po_matching",
            status="success" if not discrepancies else "discrepancy",
            message=f"Matched PO {best_match.po_number} (confidence: {best_score:.0f}%)"
            + (f", {len(discrepancies)} discrepancies" if discrepancies else ""),
        )
        db.add(log)
        await db.flush()

        logger.info(
            "Invoice %s matched to PO %s (score=%.1f, discrepancies=%d)",
            invoice_id,
            best_match.po_number,
            best_score,
            len(discrepancies),
        )

        return {
            "matched": True,
            "po_id": str(best_match.id),
            "po_number": best_match.po_number,
            "confidence": best_score / 100.0,
            "discrepancies": discrepancies,
            "match_status": match_status.value,
        }

    finally:
        if close_session:
            await db.commit()
            await db.close()


def _compare_line_items(
    invoice_items: list,
    po_items_json: str | dict | None,
) -> list[dict]:
    """Compare invoice line items against PO line items.

    Returns a list of discrepancies found.
    """
    discrepancies: list[dict] = []

    if not po_items_json:
        return []

    # Parse PO line items
    if isinstance(po_items_json, str):
        try:
            po_items = json.loads(po_items_json)
        except (json.JSONDecodeError, TypeError):
            return []
    elif isinstance(po_items_json, list):
        po_items = po_items_json
    else:
        return []

    if not po_items or not invoice_items:
        return []

    # Build a lookup by description (case-insensitive)
    po_by_desc: dict[str, dict] = {}
    for poi in po_items:
        desc = (poi.get("description") or "").strip().lower()
        if desc:
            po_by_desc[desc] = poi

    for inv_item in invoice_items:
        desc = (inv_item.description or "").strip().lower()
        if not desc:
            continue

        poi = po_by_desc.get(desc)
        if not poi:
            # Try fuzzy description match
            best_fuzzy = None
            best_fuzzy_score = 0
            for po_desc, po_data in po_by_desc.items():
                score = fuzz.ratio(desc, po_desc)
                if score > best_fuzzy_score:
                    best_fuzzy_score = score
                    best_fuzzy = po_data
            if best_fuzzy_score >= 80:
                poi = best_fuzzy
            else:
                discrepancies.append(
                    {
                        "item": desc,
                        "type": "missing_in_po",
                        "detail": f"Line item '{inv_item.description}' not found in PO",
                    }
                )
                continue

        # Compare quantity
        inv_qty = _to_float(inv_item.quantity)
        po_qty = _to_float(poi.get("quantity"))
        if inv_qty is not None and po_qty is not None and po_qty > 0:
            qty_var = abs(inv_qty - po_qty) / po_qty
            if qty_var > LINE_ITEM_VARIANCE_THRESHOLD:
                discrepancies.append(
                    {
                        "item": desc,
                        "type": "quantity_variance",
                        "expected": po_qty,
                        "actual": inv_qty,
                        "variance_pct": round(qty_var * 100, 2),
                    }
                )

        # Compare unit price
        inv_price = _to_float(inv_item.unit_price)
        po_price = _to_float(poi.get("unit_price"))
        if inv_price is not None and po_price is not None and po_price > 0:
            price_var = abs(inv_price - po_price) / po_price
            if price_var > LINE_ITEM_VARIANCE_THRESHOLD:
                discrepancies.append(
                    {
                        "item": desc,
                        "type": "unit_price_variance",
                        "expected": po_price,
                        "actual": inv_price,
                        "variance_pct": round(price_var * 100, 2),
                    }
                )

    return discrepancies


def _to_float(value: Any) -> float | None:
    """Safely convert a value to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
