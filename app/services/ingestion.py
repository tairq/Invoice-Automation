"""Ingestion service — file validation, dedup, and queue routing."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.storage import storage
from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus


class IngestionError(Exception):
    """Raised when an invoice file cannot be ingested."""


def validate_file(filename: str, content: bytes) -> str:
    """Validate file type and size. Returns the file extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in settings.allowed_extensions_list:
        raise IngestionError(
            f"File type '.{ext}' not allowed. Allowed: {', '.join(settings.allowed_extensions_list)}"
        )

    if len(content) > settings.max_file_size_bytes:
        raise IngestionError(
            f"File too large ({len(content)} bytes). Max: {settings.max_file_size_mb} MB"
        )

    return ext


async def check_duplicate(
    db: AsyncSession, content: bytes, vendor_name: Optional[str] = None
) -> bool:
    """Check if an invoice with the same content hash already exists."""
    content_hash = hashlib.sha256(content).hexdigest()

    # Simple check: same hash in filename (we store hash in metadata)
    # More advanced: check invoice_number + vendor_name after extraction
    result = await db.execute(
        select(Invoice).where(Invoice.file_path.contains(content_hash[:16]))
    )
    return result.scalar_one_or_none() is not None


async def create_invoice_record(
    db: AsyncSession,
    filename: str,
    content: bytes,
    source: InvoiceSource = InvoiceSource.upload,
    organization_id: Optional[uuid.UUID] = None,
) -> Invoice:
    """Validate, store, and create a database record for an invoice."""
    # Validate
    ext = validate_file(filename, content)

    # Generate storage path
    file_id = uuid.uuid4().hex[:12]
    content_hash = hashlib.sha256(content).hexdigest()[:16]
    storage_path = f"invoices/{file_id}_{content_hash}.{ext}"

    # Store file
    storage.save(storage_path, content)

    # Create database record
    invoice = Invoice(
        organization_id=organization_id,
        status=InvoiceStatus.pending,
        source=source,
        file_path=storage_path,
        original_filename=filename,
        file_type=ext,
        file_size=len(content),
    )

    db.add(invoice)
    await db.flush()
    return invoice
