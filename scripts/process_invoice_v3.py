"""Process the downloaded invoice through the pipeline — clean run."""
import asyncio
import logging
import os
import uuid
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("invoice_processor")

# Use the new DB file
NEW_DB = "invoice_dev_new.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./{NEW_DB}"

from app.database import engine, async_session_factory, Base
engine.echo = False

from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus, PaymentStatus
from app.models.processing_log import ProcessingLog
from app.models.extracted_data import ExtractedData
from app.models.line_item import LineItem
from app.models.extraction_confidence import ExtractionConfidence
from app.models.xero_credential import XeroCredential
from app.models.organization import Organization
from app.services.preprocessor import convert_to_images
from app.services.extractor import extract_invoice_data
from app.services.validator import validate_extraction
from app.services.vendor_matching import match_vendor
from app.services.po_matching import match_purchase_order
from app.services.payment_terms import parse_payment_terms
from app.core.storage import storage

from sqlalchemy import select
from sqlalchemy.orm import selectinload


STORAGE_PATH = Path("./storage")
ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def ensure_schema():
    """Create tables if needed."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Schema ready")


async def ensure_default_org():
    """Create default organization if not exists."""
    async with async_session_factory() as session:
        existing = await session.execute(
            select(Organization).where(Organization.id == ORG_ID)
        )
        if not existing.scalar_one_or_none():
            session.add(Organization(id=ORG_ID, name="Default Organization"))
            await session.commit()
            logger.info("Created default organization")
        else:
            logger.info("Organization exists")


async def create_invoice_record():
    """Create the invoice record in DB."""
    storage_path = "invoices/beffb83fa862_73522f3c270e58ba.pdf"
    filename = "Invoice-9BF0758D-162828.pdf"

    async with async_session_factory() as session:
        invoice = Invoice(
            organization_id=ORG_ID,
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
        await session.commit()
        logger.info("Created invoice: %s", invoice_id)
        return invoice_id


async def log_step(session, invoice_id, step, status, message):
    session.add(ProcessingLog(
        invoice_id=uuid.UUID(invoice_id),
        step=step, status=status, message=message,
    ))
    await session.flush()


async def run_pipeline(invoice_id):
    """Full processing pipeline."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Invoice).options(
                selectinload(Invoice.extracted_data),
                selectinload(Invoice.line_items),
            ).where(Invoice.id == uuid.UUID(invoice_id))
        )
        invoice = result.scalar_one_or_none()
        if not invoice:
            logger.error("Invoice %s not found", invoice_id)
            return None

        logger.info("Pipeline started for %s", invoice_id)
        invoice.status = InvoiceStatus.processing
        await session.commit()

        try:
            file_bytes = storage.read(invoice.file_path)
            logger.info("File size: %d bytes", len(file_bytes))
            images = convert_to_images(file_bytes, invoice.file_type)
            logger.info("Images: %d", len(images))
            await log_step(session, invoice_id, "preprocessing", "success",
                          "Converted to %d image(s)" % len(images))

            extraction = extract_invoice_data(images)
            ed = extraction["extracted_data"]
            logger.info("Extraction: vendor=%s total=%s %s",
                       ed.get("vendor_name"), ed.get("grand_total"), ed.get("currency"))
            await log_step(session, invoice_id, "extraction", "success",
                          "Extracted %d line items" % len(extraction.get("line_items", [])))

            validation = validate_extraction(ed, extraction["line_items"])
            logger.info("Validation: confidence=%.4f needs_review=%s",
                       validation["overall_confidence"], validation["needs_review"])
            await log_step(session, invoice_id, "validation", "success",
                          "Confidence: %.2f" % validation["overall_confidence"])

            # Save to DB
            inv_uuid = uuid.UUID(invoice_id)
            session.add(ExtractedData(
                invoice_id=inv_uuid, **ed,
                raw_extraction=extraction.get("raw_extraction"),
            ))
            for item_data in extraction["line_items"]:
                session.add(LineItem(invoice_id=inv_uuid, **item_data))
            for conf_data in extraction.get("confidence_scores", []):
                session.add(ExtractionConfidence(
                    invoice_id=inv_uuid,
                    field_name=conf_data.get("field_name", "unknown"),
                    value=str(conf_data.get("value", "")),
                    confidence=conf_data.get("confidence", 0.0),
                    method=conf_data.get("method", "llm"),
                ))
            await session.flush()
            await log_step(session, invoice_id, "save", "success", "Saved to DB")

            # Non-fatal steps
            try:
                vr = await match_vendor(invoice_id, session)
                logger.info("Vendor match: %s", vr)
            except Exception as e:
                logger.warning("Vendor matching: %s", e)

            try:
                pr = await match_purchase_order(invoice_id, session)
                logger.info("PO match: %s", pr)
            except Exception as e:
                logger.warning("PO matching: %s", e)

            try:
                ed_obj = invoice.extracted_data
                if ed_obj and ed_obj.payment_terms:
                    parsed = parse_payment_terms(ed_obj.payment_terms, ed_obj.issue_date or date.today())
                    if parsed.get("due_date"):
                        invoice.due_date = date.fromisoformat(parsed["due_date"])
                    invoice.payment_status = PaymentStatus.unpaid
            except Exception as e:
                logger.warning("Payment terms: %s", e)

            # Finalize
            if invoice.status != InvoiceStatus.needs_review:
                invoice.status = InvoiceStatus.needs_review if validation["needs_review"] else InvoiceStatus.done
            invoice.confidence_score = validation["overall_confidence"]
            invoice.needs_review = validation["needs_review"] or invoice.needs_review
            invoice.processed_at = datetime.utcnow()

            await log_step(session, invoice_id, "pipeline", "success", "Completed")
            logger.info("Pipeline done: status=%s confidence=%.4f",
                       invoice.status, validation["overall_confidence"])

        except Exception as exc:
            invoice.status = InvoiceStatus.failed
            invoice.error_message = str(exc)
            invoice.needs_review = True
            await log_step(session, invoice_id, "pipeline", "failed", str(exc))
            logger.exception("Pipeline failed")

        await session.commit()

        # Return invoice with loaded data
        await session.refresh(invoice)
        return invoice


async def push_xero_if_possible(invoice_id):
    """Try to push to Xero — expected to fail if no tokens."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Invoice).options(
                selectinload(Invoice.extracted_data),
                selectinload(Invoice.line_items),
            ).where(Invoice.id == uuid.UUID(invoice_id))
        )
        invoice = result.scalar_one_or_none()
        if not invoice:
            return None

        from app.config import settings as cfg
        if not cfg.xero_enabled:
            logger.warning("Xero disabled in config")
            return None

        cred_result = await session.execute(
            select(XeroCredential).where(XeroCredential.organization_id == invoice.organization_id)
        )
        credential = cred_result.scalar_one_or_none()
        if not credential:
            logger.warning("No Xero credential in DB. Need to re-authenticate.")
            return "NEED_AUTH"

        logger.info("Pushing to Xero tenant: %s", credential.tenant_name)
        from app.services.xero_sync import push_invoice_to_xero
        xero_id = await push_invoice_to_xero(session, invoice)
        if xero_id:
            invoice.xero_invoice_id = xero_id
            await session.commit()
            return xero_id
        return None


async def print_summary(invoice):
    """Print a formatted summary."""
    ed = invoice.extracted_data
    print("\n" + "=" * 60)
    print("INVOICE SUMMARY")
    print("=" * 60)
    print("Status:       %s" % (invoice.status.value if hasattr(invoice.status, 'value') else invoice.status))
    print("Vendor:       %s" % (ed.vendor_name if ed else "N/A"))
    print("Total:        %s %s" % (ed.grand_total if ed else "N/A", ed.currency if ed else ""))
    print("Invoice #:    %s" % (ed.invoice_number if ed else "N/A"))
    print("Confidence:   %.4f" % invoice.confidence_score if invoice.confidence_score else 0)
    print("Xero ID:      %s" % (invoice.xero_invoice_id or "Not pushed"))
    print("File:         %s" % invoice.original_filename)
    print("=" * 60)


async def main():
    await ensure_schema()
    await ensure_default_org()

    invoice_id = await create_invoice_record()
    invoice = await run_pipeline(invoice_id)

    if invoice and invoice.status == InvoiceStatus.done:
        xero_result = await push_xero_if_possible(invoice_id)
        if xero_result == "NEED_AUTH":
            print("\n⚠️  Xero authentication needed!")
            print("   The Xero access tokens were lost when the database was recreated.")
            print("   Run the auth flow to reconnect:")
            print("     python scripts/xero_auth_cli.py")
        elif xero_result:
            print("\n✅ Invoice pushed to Xero SAIW tenant!")
        else:
            print("\n⚠️  Could not push to Xero (disabled or no credentials)")

    if invoice:
        await print_summary(invoice)

    await engine.dispose()


asyncio.run(main())
