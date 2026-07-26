"""Webhook configuration API routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.organization import Organization
from app.schemas import WebhookConfigRequest

router = APIRouter(prefix="/api/v1/settings", tags=["Settings"])


@router.post("/webhook")
async def configure_webhook(
    config: WebhookConfigRequest,
    organization_id: str = "default",
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Configure webhook URL for invoice events."""
    result = await db.execute(
        select(Organization).where(Organization.id == organization_id)
    )
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
    result = await db.execute(
        select(Organization).where(Organization.id == organization_id)
    )
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
