"""Webhook configuration API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_api_key
from app.database import get_session
from app.models.organization import Organization
from app.models.webhook_delivery import WebhookDelivery
from app.schemas import WebhookConfigRequest, WebhookDeliveryResponse

router = APIRouter(
    prefix="/api/v1/settings",
    tags=["Settings"],
    dependencies=[Depends(get_api_key)],
)


@router.post("/webhook")
async def configure_webhook(
    config: WebhookConfigRequest,
    organization_id: str = "default",
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Configure webhook URL for invoice events."""
    result = await db.execute(select(Organization).where(Organization.id == organization_id))
    org = result.scalar_one_or_none()

    if not org:
        # Create default org
        org = Organization(
            id=organization_id,
            name="Default Organization",
            webhook_url=config.url,
            settings={"webhook_events": config.events},
        )
        db.add(org)
    else:
        org.webhook_url = config.url
        if org.settings is None:
            org.settings = {}
        org.settings["webhook_events"] = config.events

    return {
        "message": "Webhook configured",
        "url": config.url,
        "events": config.events,
    }


@router.get("/webhook")
async def get_webhook_config(
    organization_id: str = "default",
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Get current webhook configuration."""
    result = await db.execute(select(Organization).where(Organization.id == organization_id))
    org = result.scalar_one_or_none()

    if not org or not org.webhook_url:
        return {"configured": False, "url": None, "events": []}

    events = []
    if org.settings and "webhook_events" in org.settings:
        events = org.settings["webhook_events"]

    return {
        "configured": True,
        "url": org.webhook_url,
        "events": events,
    }


@router.get("/webhook/deliveries", response_model=list[WebhookDeliveryResponse])
async def get_webhook_deliveries(
    invoice_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_session),
) -> list[WebhookDeliveryResponse]:
    """Get webhook delivery history with optional filtering."""
    query = select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc())

    if invoice_id:
        query = query.where(WebhookDelivery.invoice_id == invoice_id)
    if status:
        query = query.where(WebhookDelivery.status == status)

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    deliveries = result.scalars().all()

    return [WebhookDeliveryResponse.model_validate(d) for d in deliveries]
