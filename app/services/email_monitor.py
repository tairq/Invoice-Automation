"""Email monitoring service — watches IMAP inbox for invoice attachments."""
from __future__ import annotations

import email
import imaplib
import logging
import os
import tempfile
from email.header import decode_header
from typing import Optional

from app.config import settings
from app.models.invoice import InvoiceSource

logger = logging.getLogger(__name__)


def decode_mime_header(header_value: str) -> str:
    """Decode a MIME encoded header value."""
    if not header_value:
        return ""
    decoded_parts = decode_header(header_value)
    parts = []
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


ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/tiff": "tiff",
}


def is_invoice_attachment(part) -> Optional[str]:
    """Check if an email part is an invoice attachment. Returns extension or None."""
    content_type = part.get_content_type()
    content_disposition = str(part.get("Content-Disposition", ""))

    if content_type not in ALLOWED_ATTACHMENT_TYPES:
        return None
    if "attachment" not in content_disposition:
        return None

    return ALLOWED_ATTACHMENT_TYPES[content_type]


async def check_email_inbox() -> int:
    """Check configured IMAP inbox and process invoice attachments.

    Returns number of invoices processed.
    """
    if not settings.email_host or not settings.email_username:
        logger.info("Email monitoring not configured — skipping")
        return 0

    processed = 0

    try:
        # Connect to IMAP
        mail = imaplib.IMAP4_SSL(settings.email_host, settings.email_port)
        mail.login(settings.email_username, settings.email_password or "")
        mail.select("INBOX")

        # Search for unseen emails
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
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

                    # Get filename
                    filename = part.get_filename()
                    if not filename:
                        filename = f"invoice_{eid.decode()}.{ext}"

                    # Decode filename if needed
                    filename = decode_mime_header(filename)

                    # Save attachment to temp file
                    attachment_data = part.get_payload(decode=True)
                    if not attachment_data:
                        continue

                    # Process the invoice
                    from app.services.ingestion import create_invoice_record
                    from app.database import async_session_factory

                    async with async_session_factory() as session:
                        invoice = await create_invoice_record(
                            db=session,
                            filename=filename,
                            content=attachment_data,
                            source=InvoiceSource.email,
                        )

                        # Queue for processing
                        from app.workers.invoice_worker import process_invoice_task

                        process_invoice_task.delay(str(invoice.id))
                        processed += 1

                        logger.info(
                            "Queued invoice %s from email: %s",
                            invoice.id, filename,
                        )

            except Exception as exc:
                logger.exception("Failed to process email %s: %s", eid, exc)

        mail.close()
        mail.logout()

    except Exception as exc:
        logger.exception("Email monitoring failed: %s", exc)

    return processed
