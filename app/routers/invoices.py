"""Invoice management API routes."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models.extracted_data import ExtractedData
from app.models.extraction_confidence import ExtractionConfidence
from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus
from app.models.line_item import LineItem
from app.models.processing_log import ProcessingLog
from app.schemas import (
    DashboardStats,
    FieldCorrection,
    InvoiceListResponse,
    InvoiceResponse,
    PaginatedResponse,
    ProcessingLogResponse,
    ReviewRequest,
    ReviewResponse,
    UploadResponse,
)
from app.services.ingestion import create_invoice_record
from app.workers.invoice_worker import process_invoice_task

router = APIRouter(prefix="/api/v1/invoices", tags=["Invoices"])


@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_invoice(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
) -> UploadResponse:
    """Upload a single invoice for processing."""
    content = await file.read()
    invoice = await create_invoice_record(
        db=db,
        filename=file.filename or "unknown",
        content=content,
        source=InvoiceSource.upload,
    )

    # Queue async processing
    process_invoice_task.delay(str(invoice.id))

    return UploadResponse(
        id=invoice.id,
        filename=invoice.original_filename,
        status=invoice.status.value,
    )


@router.post("/upload/batch", response_model=list[UploadResponse], status_code=201)
async def upload_invoices_batch(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_session),
) -> list[UploadResponse]:
    """Upload multiple invoices at once."""
    responses = []
    for file in files:
        content = await file.read()
        invoice = await create_invoice_record(
            db=db,
            filename=file.filename or "unknown",
            content=content,
            source=InvoiceSource.upload,
        )
        process_invoice_task.delay(str(invoice.id))
        responses.append(
            UploadResponse(
                id=invoice.id,
                filename=invoice.original_filename,
                status=invoice.status.value,
            )
        )
    return responses


@router.get("", response_model=PaginatedResponse)
async def list_invoices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    vendor_name: Optional[str] = Query(None),
    needs_review: Optional[bool] = Query(None),
    is_duplicate: Optional[bool] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    db: AsyncSession = Depends(get_session),
) -> PaginatedResponse:
    """List invoices with filtering, sorting, and pagination."""
    query = (
        select(Invoice)
        .options(selectinload(Invoice.extracted_data))
        .options(selectinload(Invoice.line_items))
        .options(selectinload(Invoice.confidence_scores))
        .options(selectinload(Invoice.processing_logs))
    )

    # Filters
    if status:
        query = query.where(Invoice.status == status)
    if source:
        query = query.where(Invoice.source == source)
    if vendor_name:
        query = query.where(ExtractedData.vendor_name.ilike(f"%{vendor_name}%"))
    if needs_review is not None:
        query = query.where(Invoice.needs_review == needs_review)
    if is_duplicate is not None:
        query = query.where(Invoice.is_duplicate == is_duplicate)
    if date_from:
        query = query.where(Invoice.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.where(Invoice.created_at <= datetime.fromisoformat(date_to))

    # Count total
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Sorting
    sort_col = getattr(Invoice, sort_by, Invoice.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    # Pagination
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    invoices = result.unique().scalars().all()

    items = []
    for inv in invoices:
        ed = inv.extracted_data
        items.append(InvoiceListResponse(
            id=inv.id,
            status=inv.status.value if hasattr(inv.status, "value") else inv.status,
            source=inv.source.value if hasattr(inv.source, "value") else inv.source,
            original_filename=inv.original_filename,
            file_type=inv.file_type,
            confidence_score=inv.confidence_score,
            needs_review=inv.needs_review,
            is_duplicate=inv.is_duplicate,
            vendor_name=ed.vendor_name if ed else None,
            invoice_number=ed.invoice_number if ed else None,
            grand_total=ed.grand_total if ed else None,
            currency=ed.currency if ed else None,
            created_at=inv.created_at,
        ))

    return PaginatedResponse(
        items=[item.model_dump() for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_session),
) -> DashboardStats:
    """Get aggregate invoice statistics."""
    # Total counts by status
    status_q = (
        select(Invoice.status, func.count().label("count"))
        .group_by(Invoice.status)
    )
    result = await db.execute(status_q)
    status_counts = {row.status.value if hasattr(row.status, "value") else row.status: row.count for row in result}

    # Total invoices
    total_q = select(func.count()).select_from(Invoice)
    total = (await db.execute(total_q)).scalar() or 0

    # Processed today
    today_q = select(func.count()).select_from(Invoice).where(
        func.date(Invoice.created_at) == func.current_date()
    )
    today = (await db.execute(today_q)).scalar() or 0

    # Average confidence
    conf_q = select(func.avg(Invoice.confidence_score)).where(
        Invoice.confidence_score.isnot(None)
    )
    avg_conf = (await db.execute(conf_q)).scalar() or 0.0

    # Total amount
    amt_q = select(func.sum(ExtractedData.grand_total))
    total_amt = (await db.execute(amt_q)).scalar() or 0

    # Top vendors
    vendor_q = (
        select(ExtractedData.vendor_name, func.count().label("count"))
        .group_by(ExtractedData.vendor_name)
        .order_by(func.count().desc())
        .limit(10)
    )
    vendor_result = await db.execute(vendor_q)
    top_vendors = [
        {"name": row.vendor_name, "count": row.count}
        for row in vendor_result
        if row.vendor_name
    ]

    return DashboardStats(
        total_invoices=total,
        processed_today=today,
        needs_review=status_counts.get("needs_review", 0),
        failed=status_counts.get("failed", 0),
        average_confidence=float(avg_conf),
        total_amount_processed=total_amt,
        invoices_by_status=status_counts,
        invoices_by_vendor=top_vendors,
    )


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> InvoiceResponse:
    """Get full invoice detail with extracted data and line items."""
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extracted_data))
        .options(selectinload(Invoice.line_items))
        .options(selectinload(Invoice.confidence_scores))
        .options(selectinload(Invoice.processing_logs))
        .where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Access relationships (already loaded)
    ed = invoice.extracted_data
    items = invoice.line_items or []

    return InvoiceResponse(
        id=invoice.id,
        organization_id=invoice.organization_id,
        status=invoice.status.value if hasattr(invoice.status, "value") else invoice.status,
        source=invoice.source.value if hasattr(invoice.source, "value") else invoice.source,
        original_filename=invoice.original_filename,
        file_type=invoice.file_type,
        file_size=invoice.file_size,
        confidence_score=invoice.confidence_score,
        needs_review=invoice.needs_review,
        is_duplicate=invoice.is_duplicate,
        error_message=invoice.error_message,
        processed_at=invoice.processed_at,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
        extracted_data=ed,
        line_items=items,
    )


@router.patch("/{invoice_id}/review", response_model=ReviewResponse)
async def review_invoice(
    invoice_id: uuid.UUID,
    request: ReviewRequest,
    db: AsyncSession = Depends(get_session),
) -> ReviewResponse:
    """Human review: correct extracted fields and confirm."""
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    corrections_applied = 0
    for correction in request.corrections:
        # Find existing confidence record or create one
        conf_q = select(ExtractionConfidence).where(
            ExtractionConfidence.invoice_id == invoice_id,
            ExtractionConfidence.field_name == correction.field_name,
        )
        conf_result = await db.execute(conf_q)
        confidence = conf_result.scalar_one_or_none()

        if confidence:
            confidence.corrected_value = correction.corrected_value
            confidence.reviewed = True
            confidence.reviewer_notes = correction.reviewer_notes
        else:
            confidence = ExtractionConfidence(
                invoice_id=invoice_id,
                field_name=correction.field_name,
                value=correction.corrected_value,
                corrected_value=correction.corrected_value,
                confidence=1.0,
                method="rule",
                reviewed=True,
                reviewer_notes=correction.reviewer_notes,
            )
            db.add(confidence)

        # Also update extracted_data if the field exists
        ed = invoice.extracted_data
        if ed and hasattr(ed, correction.field_name):
            try:
                setattr(ed, correction.field_name, correction.corrected_value)
            except (AttributeError, TypeError):
                pass

        corrections_applied += 1

    # Mark as done
    invoice.status = InvoiceStatus.done
    invoice.needs_review = False
    invoice.confidence_score = 1.0

    # Log review action
    db.add(ProcessingLog(
        invoice_id=invoice_id,
        step="review",
        status="success",
        message=f"Human review: {corrections_applied} corrections applied",
    ))

    return ReviewResponse(
        invoice_id=invoice_id,
        status=InvoiceStatus.done.value,
        corrections_applied=corrections_applied,
    )


@router.post("/{invoice_id}/reprocess")
async def reprocess_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run the extraction pipeline on an invoice."""
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Reset status
    invoice.status = InvoiceStatus.pending
    invoice.confidence_score = None
    invoice.needs_review = False
    invoice.error_message = None

    # Log
    db.add(ProcessingLog(
        invoice_id=invoice_id,
        step="reprocess",
        status="started",
        message="Reprocessing requested",
    ))

    # Queue async processing
    process_invoice_task.delay(str(invoice_id))

    return {"message": "Reprocessing queued", "invoice_id": str(invoice_id)}


@router.delete("/{invoice_id}")
async def delete_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Delete an invoice and its associated data."""
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Delete file from storage
    from app.core.storage import storage
    try:
        storage.delete(invoice.file_path)
    except Exception:
        pass

    await db.delete(invoice)
    return {"message": "Invoice deleted", "invoice_id": str(invoice_id)}


@router.get("/{invoice_id}/log", response_model=list[ProcessingLogResponse])
async def get_invoice_log(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> list[ProcessingLogResponse]:
    """Get processing log for an invoice."""
    result = await db.execute(
        select(ProcessingLog)
        .where(ProcessingLog.invoice_id == invoice_id)
        .order_by(ProcessingLog.created_at.asc())
    )
    logs = result.scalars().all()
    return [ProcessingLogResponse.model_validate(log) for log in logs]
