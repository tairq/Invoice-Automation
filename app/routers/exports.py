"""Export API routes — download invoice data as CSV or JSON."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.services.exporter import export_invoices_csv, export_invoices_json

router = APIRouter(prefix="/api/v1/exports", tags=["Exports"])


@router.get("/csv")
async def export_csv(
    invoice_ids: Optional[str] = Query(None, description="Comma-separated invoice IDs"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Export invoices as CSV."""
    ids = None
    if invoice_ids:
        try:
            ids = [uuid.UUID(id_str.strip()) for id_str in invoice_ids.split(",")]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid UUID: {exc}")

    from_date = date.fromisoformat(date_from) if date_from else None
    to_date = date.fromisoformat(date_to) if date_to else None

    csv_content = await export_invoices_csv(db, ids, from_date, to_date)
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoices_export.csv"},
    )


@router.get("/json")
async def export_json(
    invoice_ids: Optional[str] = Query(None, description="Comma-separated invoice IDs"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Export invoices as JSON."""
    ids = None
    if invoice_ids:
        try:
            ids = [uuid.UUID(id_str.strip()) for id_str in invoice_ids.split(",")]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid UUID: {exc}")

    from_date = date.fromisoformat(date_from) if date_from else None
    to_date = date.fromisoformat(date_to) if date_to else None

    json_content = await export_invoices_json(db, ids, from_date, to_date)
    return Response(
        content=json_content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=invoices_export.json"},
    )
