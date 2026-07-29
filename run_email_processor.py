#!/usr/bin/env python3
"""Standalone Gmail invoice processor — polls inbox, processes invoices inline.

No Celery/Redis needed. Run directly:
    python run_email_processor.py

Hits Gmail via IMAP, extracts invoice attachments, and runs the full pipeline:
preprocessing → LLM extraction → validation → DB save → Airtable sync.
"""

from __future__ import annotations

import asyncio
import email
import hashlib
import imaplib
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from email.header import decode_header
from typing import Optional

# Ensure the app package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings
from app.core.storage import storage
from app.database import async_session_factory, init_db
from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus
from app.models.processing_log import ProcessingLog

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("email_processor")

# ─── Gmail IMAP Config ──────────────────────────────────────────────

IMAP_HOST = settings.email_host or "imap.gmail.com"
IMAP_PORT = settings.email_port or 993
IMAP_USERNAME = settings.email_username or ""
IMAP_PASSWORD = settings.email_password or ""
CHECK_INTERVAL_SECONDS = settings.email_check_interval or 300  # Check every 5 minutes

ALLOWED_ATTACHMENT_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/tiff": "tiff",
}


# ─── Email Helpers ──────────────────────────────────────────────────


def decode_mime_header(header_value: str) -> str:
    """Decode a MIME encoded header value."""
    if not header_value:
        return ""
    decoded_parts = decode_header(header_value)
    parts: list[str] = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            charset = charset or "utf-8"
            try:
                parts.append(part.decode(charset))
            except (LookupError, UnicodeDecodeError):
                parts.append(part.decode("utf-8", errors="replace"))
        else:
            parts.append(part)
    return " ".join(parts)


def is_invoice_attachment(part) -> Optional[str]:
    """Check if an email part is an invoice attachment. Returns extension or None."""
    content_type = part.get_content_type()
    content_disposition = str(part.get("Content-Disposition", ""))

    if content_type not in ALLOWED_ATTACHMENT_TYPES:
        return None
    if "attachment" not in content_disposition:
        return None

    return ALLOWED_ATTACHMENT_TYPES[content_type]


# ─── Logging Helper ─────────────────────────────────────────────────


async def _log_step(session, invoice_id: str, step: str, status: str, message: str) -> None:
    """Create a processing log entry."""
    log = ProcessingLog(
        invoice_id=uuid.UUID(invoice_id),
        step=step,
        status=status,
        message=message,
    )
    session.add(log)
    await session.flush()


# ─── Pipeline Steps ─────────────────────────────────────────────────


async def process_invoice_pipeline(invoice_id: str) -> dict:
    """Run the full processing pipeline for one invoice (inline, no Celery).

    Returns dict with status, confidence, errors.
    """
    from app.models.extracted_data import ExtractedData
    from app.models.extraction_confidence import ExtractionConfidence
    from app.models.line_item import LineItem

    result: dict = {"status": "done", "confidence": 0.0, "errors": []}

    async with async_session_factory() as session:
        invoice: Optional[Invoice] = None
        extraction: Optional[dict] = None
        validation: Optional[dict] = None

        try:
            # Fetch invoice
            from sqlalchemy import select

            stmt = select(Invoice).where(Invoice.id == uuid.UUID(invoice_id))
            row = await session.execute(stmt)
            invoice = row.scalar_one_or_none()
            if not invoice:
                raise ValueError(f"Invoice {invoice_id} not found")

            invoice.status = InvoiceStatus.processing
            await session.flush()
            await _log_step(session, invoice_id, "pipeline", "started", "Processing started")
            step_start = time.time()

            # ── Step 1: Preprocessing ──
            from app.services.preprocessor import convert_to_images

            file_bytes = storage.read(invoice.file_path)
            await _log_step(
                session,
                invoice_id,
                "preprocessing",
                "started",
                f"Converting {invoice.file_type} ({len(file_bytes)} bytes)",
            )

            images = convert_to_images(file_bytes, invoice.file_type)
            await _log_step(
                session,
                invoice_id,
                "preprocessing",
                "success",
                f"Converted to {len(images)} image(s)",
            )

            # ── Step 2: AI Extraction ──
            from app.services.extractor import extract_invoice_data

            extraction = extract_invoice_data(images)
            await _log_step(
                session,
                invoice_id,
                "extraction",
                "success",
                f"Extracted {len(extraction.get('line_items', []))} line items",
            )

            # ── Step 3: Validation ──
            from app.services.validator import validate_extraction

            validation = validate_extraction(
                extraction["extracted_data"],
                extraction["line_items"],
            )
            await _log_step(
                session,
                invoice_id,
                "validation",
                "success",
                f"Confidence: {validation['overall_confidence']:.2f}, "
                f"Errors: {len(validation['validation_errors'])}, "
                f"Warnings: {len(validation['validation_warnings'])}",
            )

            # ── Step 4: Save to database ──
            inv_uuid = uuid.UUID(invoice_id)

            # Clear existing results (for reprocess safety)
            for model in [ExtractedData, LineItem, ExtractionConfidence]:
                existing = await session.execute(
                    select(model).where(model.invoice_id == inv_uuid)  # type: ignore
                )
                for item in existing.scalars().all():
                    await session.delete(item)
            await session.flush()

            # Save extracted data
            ed_data = extraction["extracted_data"]
            ed = ExtractedData(
                invoice_id=inv_uuid,
                **ed_data,
                raw_extraction=extraction.get("raw_extraction"),
            )
            session.add(ed)

            # Save line items
            for item_data in extraction["line_items"]:
                li = LineItem(invoice_id=inv_uuid, **item_data)
                session.add(li)

            # Save confidence scores
            for conf_data in extraction.get("confidence_scores", []):
                conf = ExtractionConfidence(
                    invoice_id=inv_uuid,
                    field_name=conf_data.get("field_name", "unknown"),
                    value=str(conf_data.get("value", "")),
                    confidence=conf_data.get("confidence", 0.0),
                    method=conf_data.get("method", "llm"),
                )
                session.add(conf)

            await session.flush()
            await _log_step(session, invoice_id, "save", "success", "Data saved to database")

            # ── Update invoice status ──
            invoice.status = (
                InvoiceStatus.needs_review if validation["needs_review"] else InvoiceStatus.done
            )
            invoice.confidence_score = validation["overall_confidence"]
            invoice.needs_review = validation["needs_review"]
            invoice.processed_at = datetime.utcnow()

            result["confidence"] = validation["overall_confidence"]
            result["needs_review"] = validation["needs_review"]

            # Done — commit all DB changes
            await session.commit()

            # Log pipeline success (separate commit since we already committed the main data)
            await _log_step(
                session,
                invoice_id,
                "pipeline",
                "success",
                f"Completed in {time.time() - step_start:.1f}s",
            )
            await session.commit()

            logger.info(
                "Invoice %s done: confidence=%.2f needs_review=%s",
                invoice_id,
                validation["overall_confidence"],
                validation["needs_review"],
            )

            # ── Step 5: Airtable sync (after DB commit) ──
            try:
                await _sync_to_airtable(invoice_id)
            except Exception as exc:
                logger.warning("Airtable sync failed (non-fatal): %s", exc)

        except Exception as exc:
            # Mark as failed
            try:
                if invoice:
                    invoice.status = InvoiceStatus.failed
                    invoice.error_message = str(exc)
                    invoice.needs_review = True
                    await _log_step(session, invoice_id, "pipeline", "failed", str(exc))
                    await session.commit()
            except Exception:
                try:
                    await session.rollback()
                except Exception:
                    pass

            result["status"] = "failed"
            result["errors"].append(str(exc))
            logger.exception("Pipeline failed for invoice %s", invoice_id)

    return result


async def _sync_to_airtable(invoice_id: str) -> None:
    """Push invoice data to Airtable. Awaits completion so it actually runs."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.services.airtable_sync import sync_invoice as airtable_sync

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Invoice)
                .options(
                    selectinload(Invoice.extracted_data),
                    selectinload(Invoice.line_items),
                )
                .where(Invoice.id == uuid.UUID(invoice_id))
            )
            invoice = result.scalar_one_or_none()
            if not invoice:
                logger.warning("Airtable sync: invoice %s not found", invoice_id)
                return

            ed = invoice.extracted_data
            items = invoice.line_items or []

            invoice_dict = {
                "id": str(invoice.id),
                "status": invoice.status.value
                if hasattr(invoice.status, "value")
                else invoice.status,
                "source": invoice.source.value
                if hasattr(invoice.source, "value")
                else invoice.source,
                "original_filename": invoice.original_filename,
                "file_type": invoice.file_type,
                "file_size": invoice.file_size,
                "confidence_score": invoice.confidence_score,
                "needs_review": invoice.needs_review,
                "is_duplicate": invoice.is_duplicate,
                "error_message": invoice.error_message,
                "processed_at": invoice.processed_at,
                "created_at": invoice.created_at,
                "updated_at": invoice.updated_at,
            }

            if ed:
                extracted_dict = {
                    "invoice_number": ed.invoice_number,
                    "invoice_type": ed.invoice_type,
                    "issue_date": str(ed.issue_date) if ed.issue_date else None,
                    "due_date": str(ed.due_date) if ed.due_date else None,
                    "currency": ed.currency,
                    "vendor_name": ed.vendor_name,
                    "vendor_address": ed.vendor_address,
                    "customer_name": ed.customer_name,
                    "subtotal": float(ed.subtotal) if ed.subtotal else None,
                    "tax_total": float(ed.tax_total) if ed.tax_total else None,
                    "grand_total": float(ed.grand_total) if ed.grand_total else None,
                    "amount_due": float(ed.amount_due) if ed.amount_due else None,
                    "notes": str(ed.notes) if ed.notes else None,
                }
                invoice_dict["extracted_data"] = extracted_dict

            if items:
                invoice_dict["line_items"] = [
                    {
                        "line_number": item.line_number,
                        "description": item.description,
                        "quantity": float(item.quantity) if item.quantity else None,
                        "unit": item.unit,
                        "unit_price": float(item.unit_price) if item.unit_price else None,
                        "net_amount": float(item.net_amount) if item.net_amount else None,
                        "gross_amount": float(item.gross_amount) if item.gross_amount else None,
                    }
                    for item in items
                ]

        airtable_sync(invoice_dict)

    except Exception as exc:
        logger.warning("Airtable sync failed for %s: %s", invoice_id, exc)


# ─── Ingest Attachment ──────────────────────────────────────────────


async def create_and_process_attachment(
    attachment_data: bytes, filename: str, sender: str, subject: str
) -> Optional[str]:
    """Save attachment, create DB record, and run the pipeline.

    Returns invoice_id if successful, None on failure.
    """
    from app.services.ingestion import validate_file

    try:
        ext = validate_file(filename, attachment_data)
    except Exception as e:
        logger.warning("Skipping invalid attachment %s: %s", filename, e)
        return None

    # Generate storage path (mirrors ingestion.create_invoice_record logic)
    file_id = uuid.uuid4().hex[:12]
    content_hash = hashlib.sha256(attachment_data).hexdigest()[:16]
    storage_path = f"invoices/{file_id}_{content_hash}.{ext}"

    # Store file
    storage.save(storage_path, attachment_data)

    # Create DB record
    async with async_session_factory() as session:
        invoice = Invoice(
            status=InvoiceStatus.pending,
            source=InvoiceSource.email,
            file_path=storage_path,
            original_filename=filename,
            file_type=ext,
            file_size=len(attachment_data),
        )
        session.add(invoice)
        await session.flush()
        invoice_id = str(invoice.id)

        # Log metadata from email
        await _log_step(
            session,
            invoice_id,
            "ingestion",
            "success",
            f"From: {sender}, Subject: {subject}",
        )
        await session.commit()

    logger.info("Created invoice %s from email: %s", invoice_id, filename)

    # Run pipeline
    await process_invoice_pipeline(invoice_id)

    return invoice_id


# ─── Email Check ────────────────────────────────────────────────────


async def check_and_process_emails() -> int:
    """Check Gmail inbox for unseen invoice emails. Returns count processed."""
    processed = 0

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USERNAME, IMAP_PASSWORD)
        mail.select("INBOX")

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            mail.close()
            mail.logout()
            return 0

        email_ids = messages[0].split() if messages[0] else []
        logger.info("Found %d unseen email(s)", len(email_ids))

        for eid in email_ids:
            try:
                status, msg_data = mail.fetch(eid, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = decode_mime_header(msg.get("Subject", ""))
                sender = decode_mime_header(msg.get("From", ""))

                logger.info("Processing email: %s from %s", subject, sender)

                # Extract attachments
                for part in msg.walk():
                    ext = is_invoice_attachment(part)
                    if not ext:
                        continue

                    filename = part.get_filename()
                    if not filename:
                        filename = f"invoice_{eid.decode()}.{ext}"
                    filename = decode_mime_header(filename)

                    attachment_data = part.get_payload(decode=True)
                    if not attachment_data:
                        continue

                    invoice_id = await create_and_process_attachment(
                        attachment_data,
                        filename,
                        sender,
                        subject,
                    )
                    if invoice_id:
                        processed += 1

            except Exception as exc:
                logger.exception("Failed to process email %s: %s", eid, exc)

        mail.close()
        mail.logout()

    except Exception as exc:
        logger.exception("Email check failed: %s", exc)

    return processed


# ─── Main Loop ──────────────────────────────────────────────────────


async def main() -> None:
    """Initialize DB and start the email monitoring loop."""
    logger.info("=" * 60)
    logger.info("Invoice Email Processor Starting")
    logger.info("IMAP: %s:%d as %s", IMAP_HOST, IMAP_PORT, IMAP_USERNAME)
    logger.info("DB: %s", settings.database_url)
    logger.info("Storage: %s", settings.storage_backend)
    logger.info("LLM: %s (%s)", settings.llm_provider, settings.custom_model)
    logger.info("Airtable sync: %s", settings.airtable_sync_enabled)
    logger.info("Check interval: %ds", CHECK_INTERVAL_SECONDS)
    logger.info("=" * 60)

    # Initialize database tables
    await init_db()
    logger.info("Database initialized")

    while True:
        try:
            logger.info("Checking inbox...")
            start = time.time()
            count = await check_and_process_emails()
            elapsed = time.time() - start

            if count:
                logger.info("Processed %d invoice(s) in %.1fs", count, elapsed)
            else:
                logger.debug("No invoices found (%.1fs)", elapsed)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as exc:
            logger.exception("Unexpected error in main loop: %s", exc)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped")
