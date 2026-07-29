"""Celery worker — async invoice processing pipeline."""

from __future__ import annotations

import asyncio
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
        "check-payment-due-dates": {
            "task": "check_payment_due_dates",
            "schedule": 86400.0,  # Every 24 hours
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
        result = await session.execute(select(Invoice).where(Invoice.id == uuid.UUID(invoice_id)))
        return result.scalar_one_or_none(), session


async def _run_pipeline(invoice_id: str) -> dict:
    """Execute the full processing pipeline for one invoice."""
    from app.models.invoice import InvoiceStatus

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
        except Exception as exc:
            await _log_step(session, invoice_id, "preprocessing", "failed", str(exc))
            raise

        # ── Step 3: AI Extraction ──
        try:
            from app.services.extractor import extract_invoice_data

            extraction = extract_invoice_data(images)
            await _log_step(
                session,
                invoice_id,
                "extraction",
                "success",
                f"Extracted {len(extraction.get('line_items', []))} line items",
            )
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
                session,
                invoice_id,
                "validation",
                "success",
                f"Confidence: {validation['overall_confidence']:.2f}, "
                f"Errors: {len(validation['validation_errors'])}, "
                f"Warnings: {len(validation['validation_warnings'])}",
            )
        except Exception as exc:
            await _log_step(session, invoice_id, "validation", "failed", str(exc))
            raise

        # ── Step 5: Save to database ──
        try:
            await _save_extraction_results(session, invoice_id, extraction, validation)
            await _log_step(session, invoice_id, "save", "success", "Data saved to database")
        except Exception as exc:
            await _log_step(session, invoice_id, "save", "failed", str(exc))
            raise

        # ── Step 6: Vendor Matching ──
        try:
            from app.services.vendor_matching import match_vendor

            vendor_result = await match_vendor(invoice_id, session)
            result["vendor_match"] = vendor_result
        except Exception as exc:
            logger.warning("Vendor matching failed (non-fatal): %s", exc)
            await _log_step(session, invoice_id, "vendor_matching", "failed", str(exc))

        # ── Step 7: PO / 3-Way Matching ──
        try:
            from app.services.po_matching import match_purchase_order

            po_result = await match_purchase_order(invoice_id, session)
            result["po_match"] = po_result
        except Exception as exc:
            logger.warning("PO matching failed (non-fatal): %s", exc)
            await _log_step(session, invoice_id, "po_matching", "failed", str(exc))

        # ── Step 8: Payment Terms Parsing ──
        try:
            from datetime import date

            from app.services.payment_terms import parse_payment_terms

            ed = invoice.extracted_data
            if ed and ed.payment_terms:
                parsed = parse_payment_terms(ed.payment_terms, ed.issue_date or date.today())
                if parsed.get("due_date"):
                    invoice.due_date = date.fromisoformat(parsed["due_date"])
                invoice.payment_status = "unpaid"
                await _log_step(
                    session,
                    invoice_id,
                    "payment_terms",
                    "success",
                    f"Parsed: due_date={parsed.get('due_date')}",
                )
        except Exception as exc:
            logger.warning("Payment terms parsing failed (non-fatal): %s", exc)
            await _log_step(session, invoice_id, "payment_terms", "failed", str(exc))

        # ── Step 9: Approval Workflow ──
        try:
            from app.models.invoice import ApprovalStatus
            from app.services.approval import create_approval_token, send_approval_email

            threshold = settings.approval_threshold
            grand_total = None
            if invoice.extracted_data and invoice.extracted_data.grand_total is not None:
                grand_total = float(invoice.extracted_data.grand_total)

            # If threshold is 0, ALL invoices need approval
            # If threshold > 0, only invoices above that amount need approval
            needs_approval = threshold == 0 or (grand_total is not None and grand_total > threshold)

            if needs_approval:
                token = await create_approval_token(
                    session,
                    invoice.id,
                    settings.approval_recipient_email,
                )
                vendor_name = invoice.extracted_data.vendor_name if invoice.extracted_data else None
                send_approval_email(
                    token,
                    invoice.id,
                    grand_total,
                    vendor_name,
                    settings.approval_recipient_email,
                )
                invoice.approval_status = ApprovalStatus.pending_approval
                result["approval"] = {"status": "pending", "token": token[:8] + "..."}
            else:
                invoice.approval_status = ApprovalStatus.auto_approved
                result["approval"] = {"status": "auto_approved"}
        except Exception as exc:
            logger.warning("Approval workflow failed (non-fatal): %s", exc)
            await _log_step(session, invoice_id, "approval", "failed", str(exc))

        # ── Step 10: Webhook notification (async Celery task with retry) ──
        try:
            deliver_webhook_task.delay(invoice_id)
        except Exception as exc:
            logger.warning("Webhook enqueue failed (non-fatal): %s", exc)

        # ── Step 11: Airtable sync ──
        try:
            _sync_invoice_to_airtable(invoice_id, extraction)
        except Exception as exc:
            logger.warning("Airtable sync failed (non-fatal): %s", exc)

        # ── Step 12: n8n integration ──
        if settings.n8n_enabled and settings.n8n_webhook_url:
            try:
                _trigger_n8n(invoice_id)
            except Exception as exc:
                logger.warning("n8n trigger failed (non-fatal): %s", exc)

        # ── Step 13: Xero sync (only for fully processed invoices) ──
        if invoice.status == InvoiceStatus.done:
            try:
                from app.services.xero_sync import push_invoice_to_xero

                xero_id = await push_invoice_to_xero(session, invoice)
                if xero_id:
                    invoice.xero_invoice_id = xero_id
                    await _log_step(
                        session,
                        invoice_id,
                        "xero_sync",
                        "success",
                        f"Pushed to Xero, InvoiceID={xero_id}",
                    )
                else:
                    await _log_step(
                        session,
                        invoice_id,
                        "xero_sync",
                        "skipped",
                        "Xero push skipped (disabled, no credentials, or already synced)",
                    )
            except Exception as exc:
                logger.warning("Xero sync failed (non-fatal): %s", exc)
                await _log_step(
                    session,
                    invoice_id,
                    "xero_sync",
                    "failed",
                    str(exc),
                )

        # ── Done ──
        # Don't override status if PO matching already set it to needs_review
        if invoice.status != InvoiceStatus.needs_review:
            invoice.status = (
                InvoiceStatus.needs_review if validation["needs_review"] else InvoiceStatus.done
            )
        invoice.confidence_score = validation["overall_confidence"]
        invoice.needs_review = validation["needs_review"] or invoice.needs_review
        invoice.processed_at = datetime.utcnow()

        result["confidence"] = validation["overall_confidence"]
        result["needs_review"] = validation["needs_review"]

        await _log_step(
            session,
            invoice_id,
            "pipeline",
            "success",
            f"Completed in {time.time() - step_start:.1f}s",
        )

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
    from app.models.processing_log import ProcessingLog

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

    existing_items = await session.execute(select(LineItem).where(LineItem.invoice_id == inv_uuid))
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
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.database import async_session_factory
        from app.models.invoice import Invoice

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
                "status": (
                    invoice.status.value if hasattr(invoice.status, "value") else invoice.status
                ),
                "source": (
                    invoice.source.value if hasattr(invoice.source, "value") else invoice.source
                ),
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


# ─── n8n Integration ─────────────────────────────────────────────


def _trigger_n8n(invoice_id: str) -> None:
    """Fire n8n webhook with full invoice payload (fire-and-forget).

    Only called when N8N_ENABLED=true and N8N_WEBHOOK_URL is set.
    """
    import httpx
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import async_session_factory
    from app.models.invoice import Invoice

    async def _do_trigger():
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
                logger.warning("n8n trigger: invoice %s not found", invoice_id)
                return

            ed = invoice.extracted_data
            items = invoice.line_items or []

            payload = {
                "id": str(invoice.id),
                "status": (
                    invoice.status.value if hasattr(invoice.status, "value") else invoice.status
                ),
                "vendor_name": ed.vendor_name if ed else None,
                "total_amount": float(ed.grand_total) if ed and ed.grand_total else None,
                "currency": ed.currency if ed else None,
                "due_date": ed.due_date.isoformat() if ed and ed.due_date else None,
                "airtable_record_id": getattr(invoice, "airtable_record_id", None),
                "invoice_number": ed.invoice_number if ed else None,
                "line_items": [
                    {
                        "description": item.description,
                        "quantity": float(item.quantity) if item.quantity else None,
                        "unit_price": float(item.unit_price) if item.unit_price else None,
                        "net_amount": float(item.net_amount) if item.net_amount else None,
                        "gross_amount": float(item.gross_amount) if item.gross_amount else None,
                    }
                    for item in items
                ],
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    settings.n8n_webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()

            logger.info(
                "n8n trigger: invoice %s -> %s (status=%s)",
                invoice_id,
                settings.n8n_webhook_url,
                resp.status_code,
            )

    asyncio.run(_do_trigger())


# ─── Webhook Delivery (Celery Task) ──────────────────────────────


async def _record_webhook_delivery(
    invoice_id: str,
    webhook_url: str,
    event_type: str,
    attempt_number: int,
    status: str,
    response_code: int | None = None,
    response_body: str | None = None,
    error_message: str | None = None,
) -> None:
    """Record a webhook delivery attempt in the database."""
    from app.database import async_session_factory
    from app.models.webhook_delivery import WebhookDelivery

    async with async_session_factory() as session:
        delivery = WebhookDelivery(
            invoice_id=uuid.UUID(invoice_id),
            organization_id=None,
            webhook_url=webhook_url,
            event_type=event_type,
            attempt_number=attempt_number,
            status=status,
            response_code=response_code,
            response_body=response_body,
            error_message=error_message,
            attempted_at=datetime.utcnow(),
            delivered_at=datetime.utcnow() if status == "delivered" else None,
        )
        session.add(delivery)
        await session.commit()


async def _send_admin_dead_letter_alert(invoice_id: str, webhook_url: str, error: str) -> None:
    """Send an email alert to the admin about a dead-lettered webhook."""
    recipient = settings.admin_email
    if not recipient:
        logger.warning(
            "Dead-letter alert not sent: ADMIN_EMAIL not configured (invoice=%s webhook=%s)",
            invoice_id,
            webhook_url,
        )
        return

    subject = f"[Invoice Processor] Webhook Dead Letter - Invoice {invoice_id[:8]}"
    body = (
        f"Webhook delivery has permanently failed after max retries.\n\n"
        f"Invoice ID: {invoice_id}\n"
        f"Webhook URL: {webhook_url}\n"
        f"Last error: {error}\n\n"
        f"The webhook has been moved to dead-letter status."
    )

    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.approval_from_email
        msg["To"] = recipient
        msg.set_content(body)

        if settings.smtp_host:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10.0) as smtp:
                if settings.smtp_tls:
                    smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_pass or "")
                smtp.send_message(msg)
            logger.info(
                "Dead-letter alert sent to %s for invoice %s",
                recipient,
                invoice_id,
            )
        else:
            logger.warning(
                "SMTP not configured; dead-letter alert would have been sent to %s. "
                "Subject: %s Body: %s",
                recipient,
                subject,
                body,
            )
    except Exception as exc:
        logger.warning("Failed to send dead-letter alert email: %s", exc)


@celery_app.task(
    bind=True,
    name="deliver_webhook",
    autoretry_for=(Exception,),
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=300,
    acks_late=True,
)
def deliver_webhook_task(self, invoice_id: str) -> dict | None:
    """Deliver webhook notification with retry and dead-letter tracking.

    Retries on any exception with exponential backoff (max 5 min).
    After exhausting retries, the delivery is marked as dead_letter
    and an admin alert email is sent.
    """
    attempt = self.request.retries + 1
    is_last_attempt = self.request.retries >= self.max_retries
    logger.info(
        "Webhook delivery attempt %d/%d for invoice %s",
        attempt,
        self.max_retries + 1,
        invoice_id,
    )

    webhook_url: str | None = None

    try:
        from app.database import async_session_factory
        from app.models.organization import Organization

        async def _fetch_webhook_url() -> str | None:
            async with async_session_factory() as s:
                result = await s.execute(select(Organization).where(Organization.id == "default"))
                org = result.scalar_one_or_none()
                return org.webhook_url if org else None

        webhook_url = asyncio.run(_fetch_webhook_url())
        if not webhook_url:
            logger.info("No webhook configured for invoice %s, skipping", invoice_id)
            return None

        import httpx

        async def _build_and_send():
            from sqlalchemy.orm import selectinload

            from app.models.invoice import Invoice

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
                    raise ValueError(f"Invoice {invoice_id} not found")

                ed = invoice.extracted_data
                items = invoice.line_items or []

                event = "invoice.needs_review" if invoice.needs_review else "invoice.processed"
                payload = {
                    "event": event,
                    "invoice_id": invoice_id,
                    "confidence": invoice.confidence_score,
                    "needs_review": invoice.needs_review,
                    "extracted": {
                        "invoice_number": ed.invoice_number if ed else None,
                        "vendor_name": ed.vendor_name if ed else None,
                        "grand_total": str(ed.grand_total) if ed and ed.grand_total else "",
                        "currency": ed.currency if ed else None,
                        "line_items_count": len(items),
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                }

                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(webhook_url, json=payload)
                    resp.raise_for_status()
                    return resp

        resp = asyncio.run(_build_and_send())

        asyncio.run(
            _record_webhook_delivery(
                invoice_id=invoice_id,
                webhook_url=webhook_url,
                event_type="invoice.processed",
                attempt_number=attempt,
                status="delivered",
                response_code=resp.status_code,
                response_body=resp.text[:2000],
            )
        )

        logger.info("Webhook delivered for invoice %s (attempt %d)", invoice_id, attempt)
        return {"status": "delivered", "attempt": attempt}

    except Exception as exc:
        error_msg = str(exc)[:500]

        try:
            asyncio.run(
                _record_webhook_delivery(
                    invoice_id=invoice_id,
                    webhook_url=webhook_url or "unknown",
                    event_type="invoice.processed",
                    attempt_number=attempt,
                    status="dead_letter" if is_last_attempt else "failed",
                    response_code=None,
                    error_message=error_msg,
                )
            )
        except Exception as log_exc:
            logger.warning("Failed to record webhook delivery attempt: %s", log_exc)

        if is_last_attempt:
            logger.error(
                "Webhook dead-letter for invoice %s after %d attempts: %s",
                invoice_id,
                attempt,
                error_msg,
            )
            try:
                asyncio.run(
                    _send_admin_dead_letter_alert(
                        invoice_id,
                        webhook_url or "unknown",
                        error_msg,
                    )
                )
            except Exception as alert_exc:
                logger.warning("Failed to send dead-letter alert: %s", alert_exc)
        else:
            logger.warning(
                "Webhook attempt %d failed for invoice %s, will retry: %s",
                attempt,
                invoice_id,
                error_msg,
            )

        raise


# ─── Celery Tasks ─────────────────────────────────────────────────


@celery_app.task(bind=True, name="process_invoice", max_retries=3)
def process_invoice_task(self, invoice_id: str):
    """Process a single invoice through the full pipeline."""
    try:
        result = asyncio.run(_run_pipeline(invoice_id))
        logger.info(
            "Invoice %s processed: status=%s confidence=%.2f",
            invoice_id,
            result["status"],
            result["confidence"],
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


@celery_app.task(name="check_payment_due_dates")
def check_payment_due_dates_task():
    """Daily task: update overdue invoices and send payment reminders.

    - Invoices where due_date < today → payment_status = "overdue"
    - Invoices where due_date = today + 3 days → send reminder email
    """
    import asyncio

    asyncio.run(_run_payment_due_date_check())


async def _run_payment_due_date_check():
    """Check all unpaid invoices and update payment statuses."""
    from datetime import date, timedelta

    from sqlalchemy import or_, select
    from sqlalchemy.orm import selectinload

    from app.database import async_session_factory
    from app.models.invoice import Invoice, PaymentStatus
    from app.models.processing_log import ProcessingLog

    today = date.today()
    reminder_date = today + timedelta(days=3)

    async with async_session_factory() as session:
        # Find all unpaid invoices with a due date
        result = await session.execute(
            select(Invoice)
            .options(selectinload(Invoice.extracted_data))
            .where(
                Invoice.due_date.isnot(None),
                or_(
                    Invoice.payment_status == PaymentStatus.unpaid,
                    Invoice.payment_status.is_(None),
                ),
            )
        )
        invoices = result.scalars().all()

        for invoice in invoices:
            try:
                if invoice.due_date and invoice.due_date < today:
                    # Mark as overdue
                    invoice.payment_status = PaymentStatus.overdue
                    log = ProcessingLog(
                        invoice_id=invoice.id,
                        step="payment_check",
                        status="overdue",
                        message=f"Payment overdue since {invoice.due_date}",
                    )
                    session.add(log)
                    logger.info(
                        "Invoice %s marked overdue (due: %s)",
                        invoice.id,
                        invoice.due_date,
                    )

                elif invoice.due_date and invoice.due_date == reminder_date:
                    # Send reminder email (3 days before due)
                    from app.services.approval import send_payment_reminder_email

                    ed = invoice.extracted_data
                    amount_due = float(ed.amount_due) if ed and ed.amount_due else None

                    send_payment_reminder_email(
                        invoice_id=invoice.id,
                        vendor_name=ed.vendor_name if ed else None,
                        due_date=invoice.due_date.isoformat(),
                        amount_due=amount_due,
                        recipient_email=settings.payment_reminder_email,
                    )
                    log = ProcessingLog(
                        invoice_id=invoice.id,
                        step="payment_check",
                        status="reminder_sent",
                        message=f"Payment reminder sent for due date {invoice.due_date}",
                    )
                    session.add(log)
                    logger.info(
                        "Payment reminder sent for invoice %s (due: %s)",
                        invoice.id,
                        invoice.due_date,
                    )
            except Exception as exc:
                logger.warning(
                    "Payment check failed for invoice %s: %s",
                    invoice.id,
                    exc,
                )

        await session.commit()
