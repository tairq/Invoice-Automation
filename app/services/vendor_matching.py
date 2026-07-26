"""Vendor master matching service — match extracted vendor names against the vendor master list."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings

logger = logging.getLogger(__name__)

VENDOR_MATCH_THRESHOLD = 80  # percent


async def match_vendor(invoice_id: str, db: AsyncSession | None = None) -> dict:
    """Match the extracted vendor name against the vendor master list.

    Returns a dict with match result information.
    """
    from app.models.extracted_data import ExtractedData
    from app.models.invoice import Invoice
    from app.models.processing_log import ProcessingLog
    from app.models.vendor import Vendor

    close_session = False
    if db is None:
        from app.database import async_session_factory

        db = async_session_factory()
        close_session = True

    try:
        # Convert string ID to UUID (required for SQLite compatibility)
        inv_uuid = uuid.UUID(invoice_id)

        # Load invoice with extracted data
        result = await db.execute(
            select(Invoice)
            .options(selectinload(Invoice.extracted_data))
            .where(Invoice.id == inv_uuid)
        )
        invoice = result.scalar_one_or_none()
        if not invoice:
            logger.warning("Invoice %s not found for vendor matching", invoice_id)
            return {"matched": False, "reason": "Invoice not found"}

        ed = invoice.extracted_data
        if not ed or not ed.vendor_name:
            logger.info("Invoice %s has no vendor name — skipping vendor match", invoice_id)
            return {"matched": False, "reason": "No vendor name extracted"}

        # Load all vendors
        vendor_result = await db.execute(select(Vendor))
        all_vendors = vendor_result.scalars().all()

        if not all_vendors:
            logger.info("No vendors in master list to match against")
            ed.vendor_verified = False
            await db.flush()
            return {"matched": False, "reason": "No vendors in master list"}

        extracted_name = ed.vendor_name.strip().lower()

        best_vendor = None
        best_score = 0.0

        for vendor in all_vendors:
            # Build names to compare: canonical + aliases
            names_to_check = [vendor.canonical_name.lower()]
            if vendor.aliases:
                if isinstance(vendor.aliases, str):
                    try:
                        import json
                        aliases = json.loads(vendor.aliases)
                    except (json.JSONDecodeError, TypeError):
                        aliases = []
                elif isinstance(vendor.aliases, list):
                    aliases = vendor.aliases
                else:
                    aliases = []
                names_to_check.extend(str(a).lower() for a in aliases if a)

            # Score against each name, take the best
            for name in names_to_check:
                # Use token_sort_ratio for multi-word names
                score = fuzz.token_sort_ratio(extracted_name, name)
                if score > best_score:
                    best_score = score
                    best_vendor = vendor

        match_confidence = best_score / 100.0

        if best_vendor and best_score >= VENDOR_MATCH_THRESHOLD:
            # Match found — link invoice and mark verified
            invoice.vendor_id = best_vendor.id
            invoice.match_confidence = match_confidence
            ed.vendor_id = best_vendor.id
            ed.vendor_verified = True

            log_msg = f"Matched vendor '{ed.vendor_name}' → {best_vendor.canonical_name} (confidence: {best_score:.0f}%)"
            logger.info("Invoice %s: %s", invoice_id, log_msg)

            log = ProcessingLog(
                invoice_id=invoice.id,
                step="vendor_matching",
                status="success",
                message=log_msg,
            )
            db.add(log)
            await db.flush()

            return {
                "matched": True,
                "vendor_id": str(best_vendor.id),
                "canonical_name": best_vendor.canonical_name,
                "confidence": match_confidence,
            }
        else:
            # No match
            ed.vendor_verified = False
            invoice.match_confidence = match_confidence

            log_msg = (
                f"No vendor match for '{ed.vendor_name}' "
                f"(best: {best_vendor.canonical_name if best_vendor else 'N/A'} "
                f"at {best_score:.0f}%)"
            )
            logger.info("Invoice %s: %s", invoice_id, log_msg)

            log = ProcessingLog(
                invoice_id=invoice.id,
                step="vendor_matching",
                status="no_match",
                message=log_msg,
            )
            db.add(log)
            await db.flush()

            return {
                "matched": False,
                "best_candidate": best_vendor.canonical_name if best_vendor else None,
                "best_score": match_confidence,
                "reason": "Below confidence threshold",
            }

    finally:
        if close_session:
            await db.commit()
            await db.close()
