"""Create a fresh database and process the invoice from Gmail."""

import asyncio
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Use a new DB file (old one is locked)
NEW_DB = "invoice_dev_new.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./{NEW_DB}"

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("invoice_processor")

# Import app modules after env is set
from app.config import settings as app_settings

settings = app_settings  # use the real settings from .env

from app.database import Base, async_session_factory, engine

engine.echo = False  # Disable SQL echo for cleaner output
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.storage import storage
from app.models.extracted_data import ExtractedData
from app.models.extraction_confidence import ExtractionConfidence
from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus, PaymentStatus
from app.models.line_item import LineItem
from app.models.organization import Organization
from app.models.processing_log import ProcessingLog
from app.models.xero_credential import XeroCredential
from app.services.extractor import extract_invoice_data
from app.services.payment_terms import parse_payment_terms
from app.services.po_matching import match_purchase_order
from app.services.preprocessor import convert_to_images
from app.services.validator import validate_extraction
from app.services.vendor_matching import match_vendor

STORAGE_PATH = Path("./storage")


async def log_step(session, invoice_id, step, status, message):
    session.add(
        ProcessingLog(
            invoice_id=uuid.UUID(invoice_id),
            step=step,
            status=status,
            message=message,
        )
    )
    await session.flush()


async def save_extraction_results(session, invoice_id, extraction, _validation):
    inv_uuid = uuid.UUID(invoice_id)
    ed_data = extraction["extracted_data"]

    # Delete any existing records for this invoice
    for model in [ExtractedData, LineItem, ExtractionConfidence]:
        existing = await session.execute(select(model).where(model.invoice_id == inv_uuid))
        for row in existing.scalars().all():
            await session.delete(row)
    await session.flush()

    session.add(
        ExtractedData(
            invoice_id=inv_uuid,
            **ed_data,
            raw_extraction=extraction.get("raw_extraction"),
        )
    )

    for item_data in extraction["line_items"]:
        session.add(LineItem(invoice_id=inv_uuid, **item_data))

    for conf_data in extraction.get("confidence_scores", []):
        session.add(
            ExtractionConfidence(
                invoice_id=inv_uuid,
                field_name=conf_data.get("field_name", "unknown"),
                value=str(conf_data.get("value", "")),
                confidence=conf_data.get("confidence", 0.0),
                method=conf_data.get("method", "llm"),
            )
        )
    await session.flush()


async def run_pipeline(invoice_id: str):
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
            logger.error("Invoice %s not found", invoice_id)
            return

        logger.info("Pipeline started for invoice %s", invoice_id)
        invoice.status = InvoiceStatus.processing
        await session.commit()

        try:
            file_bytes = storage.read(invoice.file_path)
            logger.info("Read file: %d bytes", len(file_bytes))

            images = convert_to_images(file_bytes, invoice.file_type)
            logger.info("Converted to %d image(s)", len(images))
            await log_step(
                session,
                invoice_id,
                "preprocessing",
                "success",
                "Converted to %d image(s)" % len(images),
            )

            extraction = extract_invoice_data(images)
            ed = extraction["extracted_data"]
            logger.info(
                "Extracted: vendor=%s, total=%s", ed.get("vendor_name"), ed.get("grand_total")
            )
            await log_step(
                session,
                invoice_id,
                "extraction",
                "success",
                "Extracted %d line items" % len(extraction.get("line_items", [])),
            )

            validation = validate_extraction(ed, extraction["line_items"])
            logger.info(
                "Validation: confidence=%.4f, needs_review=%s",
                validation["overall_confidence"],
                validation["needs_review"],
            )
            await log_step(
                session,
                invoice_id,
                "validation",
                "success",
                "Confidence: %.2f" % validation["overall_confidence"],
            )

            await save_extraction_results(session, invoice_id, extraction, validation)
            await log_step(session, invoice_id, "save", "success", "Saved to DB")

            for label, func in [
                ("vendor_matching", lambda: match_vendor(invoice_id, session)),
                ("po_matching", lambda: match_purchase_order(invoice_id, session)),
            ]:
                try:
                    r = await func()
                    logger.info("%s: %s", label, r)
                except Exception as e:
                    logger.warning("%s failed: %s", label, e)

            try:
                ed_obj = invoice.extracted_data
                if ed_obj and ed_obj.payment_terms:
                    parsed = parse_payment_terms(
                        ed_obj.payment_terms, ed_obj.issue_date or date.today()
                    )
                    if parsed.get("due_date"):
                        invoice.due_date = date.fromisoformat(parsed["due_date"])
                    invoice.payment_status = PaymentStatus.unpaid
            except Exception as e:
                logger.warning("Payment terms failed: %s", e)

            if invoice.status != InvoiceStatus.needs_review:
                invoice.status = (
                    InvoiceStatus.needs_review if validation["needs_review"] else InvoiceStatus.done
                )
            invoice.confidence_score = validation["overall_confidence"]
            invoice.needs_review = validation["needs_review"] or invoice.needs_review
            invoice.processed_at = datetime.utcnow()

            await log_step(session, invoice_id, "pipeline", "success", "Pipeline completed")
            logger.info(
                "Pipeline done: status=%s, confidence=%.4f",
                invoice.status,
                validation["overall_confidence"],
            )

        except Exception as exc:
            invoice.status = InvoiceStatus.failed
            invoice.error_message = str(exc)
            invoice.needs_review = True
            await log_step(session, invoice_id, "pipeline", "failed", str(exc))
            logger.exception("Pipeline failed: %s", exc)

        await session.commit()


async def push_to_xero(invoice_id):
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
            logger.error("Invoice %s not found", invoice_id)
            return

        if not settings.xero_enabled or not settings.xero_client_id:
            logger.warning("Xero not configured, skipping")
            return

        if not invoice.organization_id:
            invoice.organization_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
            await session.flush()

        cred_result = await session.execute(
            select(XeroCredential).where(XeroCredential.organization_id == invoice.organization_id)
        )
        credential = cred_result.scalar_one_or_none()
        if not credential:
            logger.error("No Xero credential found")
            return

        logger.info(
            "Credential found: tenant=%s (%s)", credential.tenant_name, credential.tenant_id
        )

        # Refresh token if needed
        import httpx

        now = datetime.now(timezone.utc)
        expires_at = credential.token_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if not expires_at or expires_at <= now + timedelta(minutes=5):
            logger.info("Refreshing Xero token...")
            data = {
                "grant_type": "refresh_token",
                "refresh_token": credential.refresh_token,
                "client_id": settings.xero_client_id,
            }
            if settings.xero_client_secret:
                data["client_secret"] = settings.xero_client_secret
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://identity.xero.com/connect/token", data=data, timeout=15.0
                )
                resp.raise_for_status()
                td = resp.json()
            credential.access_token = td["access_token"]
            credential.refresh_token = td.get("refresh_token", credential.refresh_token)
            credential.token_expires_at = now + timedelta(seconds=td.get("expires_in", 1800))
            await session.flush()
            logger.info("Token refreshed")

        from app.services.xero_client import XeroClient

        client = XeroClient(credential)
        xero_id = client.push_invoice(invoice)
        if xero_id:
            invoice.xero_invoice_id = xero_id
            await session.flush()
            logger.info("Xero push succeeded: InvoiceID=%s", xero_id)
        else:
            logger.error("Xero push failed")

        await session.commit()


async def main():
    # 1. Create all tables fresh from models
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Created all tables from current models")

    # 2. Restore backup data (org + xero cred)
    with open("scripts/db_backup.json") as f:
        backup = json.load(f)

    async with async_session_factory() as session:
        for org_data in backup["organizations"]:
            session.add(Organization(id=uuid.UUID(org_data["id"]), name=org_data["name"]))
        logger.info("Restored organization")

        for cred_data in backup["xero_credentials"]:
            session.add(
                XeroCredential(
                    organization_id=uuid.UUID(cred_data["organization_id"]),
                    access_token=cred_data["access_token"],
                    refresh_token=cred_data["refresh_token"],
                    token_expires_at=datetime.fromisoformat(cred_data["token_expires_at"]),
                    tenant_id=cred_data["tenant_id"],
                    tenant_name=cred_data.get("tenant_name"),
                )
            )
        logger.info("Restored Xero credential")
        await session.commit()

    # 3. Create invoice record
    storage_path = "invoices/beffb83fa862_73522f3c270e58ba.pdf"
    filename = "Invoice-9BF0758D-162828.pdf"

    async with async_session_factory() as session:
        invoice = Invoice(
            organization_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            status=InvoiceStatus.pending,
            source=InvoiceSource.email,
            file_path=storage_path,
            original_filename=filename,
            file_type="pdf",
            file_size=Path(STORAGE_PATH / storage_path).stat().st_size,
        )
        session.add(invoice)
        await session.flush()
        invoice_id = str(invoice.id).replace("-", "")
        logger.info("Created invoice record: %s", invoice_id)
        await session.commit()

    # 4. Run pipeline
    await run_pipeline(invoice_id)

    # 5. Push to Xero
    await push_to_xero(invoice_id)

    # 6. Summary
    async with async_session_factory() as session:
        result = await session.execute(
            select(Invoice)
            .options(
                selectinload(Invoice.extracted_data),
            )
            .where(Invoice.id == uuid.UUID(invoice_id))
        )
        inv = result.scalar_one_or_none()
        if inv:
            ed = inv.extracted_data
            print("\n" + "=" * 60)
            print("INVOICE SUMMARY")
            print("=" * 60)
            print(
                "Status:       %s"
                % (inv.status.value if hasattr(inv.status, "value") else inv.status)
            )
            print("Vendor:       %s" % (ed.vendor_name if ed else "N/A"))
            print(
                "Total:        %s %s" % (ed.grand_total if ed else "N/A", ed.currency if ed else "")
            )
            print("Invoice #:    %s" % (ed.invoice_number if ed else "N/A"))
            print("Confidence:   %.4f" % inv.confidence_score)
            print("Xero ID:      %s" % (inv.xero_invoice_id or "Not pushed"))
            print("File:         %s" % inv.original_filename)
            print("=" * 60)

    await engine.dispose()


asyncio.run(main())
