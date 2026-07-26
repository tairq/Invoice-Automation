"""Celery worker — async invoice processing pipeline."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime

from celery import Celery
from sqlalchemy import select

from app.config import settings
from app.core.storage import storage

# Celery app
celery_app = Celery(
    "invoice_processor",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_soft_time_limit=600,
    task_time_limit=900,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "check-email": {
            "task": "check_email",
            "schedule": 300.0,  # Every 5 minutes
            "options": {"queue": "monitoring"},
        },
        "cleanup-temp": {
            "task": "cleanup_temp",
            "schedule": 3600.0,  # Every hour
            "options": {"queue": "maintenance"},
        },
    },
)

logger = logging.getLogger(__name__)


# ─── Helper: async DB session ────────────────────────────────────

async def _get_invoice(invoice_id: str):
    """Get invoice from DB using async session."""
    from app.database import async_session_factory
    from app.models.invoice import Invoice

    async with async_session_factory() as session:
        result = await session.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(invoice_id))
        )
        return result.scalar_one_or_none(), session


async def _run_pipeline(invoice_id: str) -> dict:
    """Execute the full processing pipeline for one invoice."""
    from app.models.invoice import Invoice, InvoiceStatus
    from app.models.processing_log import ProcessingLog

    invoice, session = await _get_invoice(invoice_id)
    if not invoice:
        raise ValueError(f"Invoice {invoice_id} not found")

    result = {"status": "done", "confidence": 0.0, "errors": []}

    try:
        # ── Step 1: Mark as processing ──
        await _log_step(session, invoice_id, "pipeline", "started", "Processing started")
        invoice.status = InvoiceStatus.processing
        await session.commit()
        step_start = time.time()

        # ── Step 2: Preprocessing (convert to images) ──
        try:
            from app.services.preprocessor import convert_to_images, enhance_images

            file_bytes = storage.read(invoice.file_path)
            await _log_step(session, invoice_id, "preprocessing", "started",
                          f"Converting {invoice.file_type} ({len(file_bytes)} bytes)")

            images = convert_to_images(file_bytes, invoice.file_type)
            await _log_step(session, invoice_id, "preprocessing", "success",
                          f"Converted to {len(images)} image(s)")
        except Exception as exc:
            await _log_step(session, invoice_id, "preprocessing", "failed", str(exc))
            raise

        # ── Step 3: AI Extraction ──
        try:
            from app.services.extractor import extract_invoice_data

            extraction = extract_invoice_data(images)
            await _log_step(session, invoice_id, "extraction", "success",
                          f"Extracted {len(extraction.get('line_items', []))} line items")
        except Exception as exc:
            await _log_step(session, invoice_id, "extraction", "failed", str(exc))
            raise

        # ── Step 4: Validation ──
        try:
            from app.services.validator import validate_extraction

            validation = validate_extraction(
                extraction["extracted_data"],
                extraction["line_items"],
            )

            await _log_step(
                session, invoice_id, "validation", "success",
                f"Confidence: {validation['overall_confidence']:.2f}, "
                f"Errors: {len(validation['validation_errors'])}, "
                f"Warnings: {len(validation['validation_warnings'])}",
            )
        except Exception as exc:
            await _log_step(session, invoice_id, "validation", "failed", str(exc))
            raise

        # ── Step 5: Save to database ──
        try:
            await _save_extraction_results(
                session, invoice_id, extraction, validation
            )
            await _log_step(session, invoice_id, "save", "success", "Data saved to database")
        except Exception as exc:
            await _log_step(session, invoice_id, "save", "failed", str(exc))
            raise

        # ── Step 6: Webhook notification ──
        try:
            if validation["needs_review"]:
                webhook_event = "invoice.needs_review"
            else:
                webhook_event = "invoice.processed"

            _fire_webhook(invoice_id, webhook_event, extraction, validation)
        except Exception as exc:
            logger.warning("Webhook failed (non-fatal): %s", exc)

        # ── Step 7: Airtable sync ──
        try:
            _sync_invoice_to_airtable(invoice_id, extraction)
        except Exception as exc:
            logger.warning("Airtable sync failed (non-fatal): %s", exc)

        # ── Done ──
        invoice.status = (
            InvoiceStatus.needs_review
            if validation["needs_review"]
            else InvoiceStatus.done
        )
        invoice.confidence_score = validation["overall_confidence"]
        invoice.needs_review = validation["needs_review"]
        invoice.processed_at = datetime.utcnow()

        result["confidence"] = validation["overall_confidence"]
        result["needs_review"] = validation["needs_review"]

        await _log_step(session, invoice_id, "pipeline", "success",
                      f"Completed in {time.time() - step_start:.1f}s")

    except Exception as exc:
        invoice.status = InvoiceStatus.failed
        invoice.error_message = str(exc)
        invoice.needs_review = True

        await _log_step(session, invoice_id, "pipeline", "failed", str(exc))
        result["status"] = "failed"
        result["errors"].append(str(exc))
        logger.exception("Pipeline failed for invoice %s", invoice_id)

    finally:
        await session.commit()
        await session.close()

    return result


async def _log_step(session, invoice_id: str, step: str, status: str, message: str):
    """Create a processing log entry."""
    log = ProcessingLog(
        invoice_id=uuid.UUID(invoice_id),
        step=step,
        status=status,
        message=message,
    )
    session.add(log)
    await session.commit()


async def _save_extraction_results(session, invoice_id: str, extraction: dict, validation: dict):
    """Save extraction results to the database."""
    from app.models.extracted_data import ExtractedData
    from app.models.extraction_confidence import ExtractionConfidence
    from app.models.line_item import LineItem

    inv_uuid = uuid.UUID(invoice_id)

    # Delete existing results (for reprocess)
    existing = await session.execute(
        select(ExtractedData).where(ExtractedData.invoice_id == inv_uuid)
    )
    if ed := existing.scalar_one_or_none():
        await session.delete(ed)

    existing_items = await session.execute(
        select(LineItem).where(LineItem.invoice_id == inv_uuid)
    )
    for item in existing_items.scalars().all():
        await session.delete(item)

    existing_conf = await session.execute(
        select(ExtractionConfidence).where(ExtractionConfidence.invoice_id == inv_uuid)
    )
    for conf in existing_conf.scalars().all():
        await session.delete(conf)

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


def _sync_invoice_to_airtable(invoice_id: str, extraction: dict) -> None:
    """Push invoice data to Airtable (fire-and-forget)."""
    from app.services.airtable_sync import sync_invoice

    async def _build_and_sync():
        from app.database import async_session_factory
        from app.models.extracted_data import ExtractedData
        from app.models.invoice import Invoice
        from app.models.line_item import LineItem
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

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
                return

            # Build a dict representation for the sync service
            ed = invoice.extracted_data
            items = invoice.line_items or []

            invoice_dict = {
                "id": str(invoice.id),
                "status": invoice.status.value if hasattr(invoice.status, "value") else invoice.status,
                "source": invoice.source.value if hasattr(invoice.source, "value") else invoice.source,
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
                    "issue_date": ed.issue_date,
                    "due_date": ed.due_date,
                    "period_start": ed.period_start,
                    "period_end": ed.period_end,
                    "currency": ed.currency,
                    "language": ed.language,
                    "payment_terms": ed.payment_terms,
                    "po_number": ed.po_number,
                    "notes": ed.notes,
                    "vendor_name": ed.vendor_name,
                    "vendor_address": ed.vendor_address,
                    "vendor_tax_id": ed.vendor_tax_id,
                    "vendor_email": ed.vendor_email,
                    "vendor_phone": ed.vendor_phone,
                    "vendor_bank_name": ed.vendor_bank_name,
                    "vendor_bank_account": ed.vendor_bank_account,
                    "vendor_bank_iban": ed.vendor_bank_iban,
                    "vendor_bank_swift": ed.vendor_bank_swift,
                    "customer_name": ed.customer_name,
                    "customer_address": ed.customer_address,
                    "customer_tax_id": ed.customer_tax_id,
                    "subtotal": ed.subtotal,
                    "tax_total": ed.tax_total,
                    "discount_total": ed.discount_total,
                    "grand_total": ed.grand_total,
                    "amount_due": ed.amount_due,
                    "amount_paid": ed.amount_paid,
                }
                invoice_dict["extracted_data"] = extracted_dict

            if items:
                invoice_dict["line_items"] = [
                    {
                        "line_number": item.line_number,
                        "description": item.description,
                        "quantity": item.quantity,
                        "unit": item.unit,
                        "unit_price": item.unit_price,
                        "tax_rate": item.tax_rate,
                        "tax_amount": item.tax_amount,
                        "discount_amount": item.discount_amount,
                        "net_amount": item.net_amount,
                        "gross_amount": item.gross_amount,
                        "item_code": item.item_code,
                    }
                    for item in items
                ]

            sync_invoice(invoice_dict)

    asyncio.run(_build_and_sync())


def _fire_webhook(invoice_id: str, event: str, extraction: dict, validation: dict):
    """Fire webhook notification (fire-and-forget)."""
    try:
        import httpx

        # Get webhook config from DB (simplified — use default org)
        from app.database import async_session_factory
        from app.models.organization import Organization

        async def _get_webhook_url():
            async with async_session_factory() as s:
                result = await s.execute(
                    select(Organization).where(Organization.id == "default")
                )
                org = result.scalar_one_or_none()
                return org.webhook_url if org else None

        webhook_url = asyncio.run(_get_webhook_url())
        if not webhook_url:
            return

        payload = {
            "event": event,
            "invoice_id": invoice_id,
            "confidence": validation.get("overall_confidence"),
            "needs_review": validation.get("needs_review", False),
            "extracted": {
                "invoice_number": extraction.get("extracted_data", {}).get("invoice_number"),
                "vendor_name": extraction.get("extracted_data", {}).get("vendor_name"),
                "grand_total": str(extraction.get("extracted_data", {}).get("grand_total", "")),
                "currency": extraction.get("extracted_data", {}).get("currency"),
                "line_items_count": len(extraction.get("line_items", [])),
            },
            "timestamp": datetime.utcnow().isoformat(),
        }

        httpx.post(webhook_url, json=payload, timeout=10.0)
    except Exception as exc:
        logger.warning("Webhook delivery failed: %s", exc)


# ─── Celery Tasks ─────────────────────────────────────────────────

@celery_app.task(bind=True, name="process_invoice", max_retries=3)
def process_invoice_task(self, invoice_id: str):
    """Process a single invoice through the full pipeline."""
    try:
        result = asyncio.run(_run_pipeline(invoice_id))
        logger.info(
            "Invoice %s processed: status=%s confidence=%.2f",
            invoice_id, result["status"], result["confidence"],
        )
        return result
    except Exception as exc:
        logger.exception("Fatal error processing invoice %s", invoice_id)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="check_email")
def check_email_task():
    """Poll configured IMAP inbox for new invoice emails."""
    from app.services.email_monitor import check_email_inbox

    asyncio.run(check_email_inbox())


@celery_app.task(name="cleanup_temp")
def cleanup_temp_task():
    """Clean up temporary files older than 24 hours."""
    import os
    import tempfile
    import time

    now = time.time()
    temp_dir = tempfile.gettempdir()
    for f in os.listdir(temp_dir):
        fpath = os.path.join(temp_dir, f)
        if f.startswith("invoice_") and os.path.isfile(fpath):
            if now - os.path.getmtime(fpath) > 86400:
                try:
                    os.unlink(fpath)
                except Exception:
                    pass
