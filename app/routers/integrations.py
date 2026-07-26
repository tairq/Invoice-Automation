"""Integration API routes — Xero OAuth flow."""
from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_api_key
from app.database import get_session
from app.models.xero_credential import XeroCredential
from app.services.xero_sync import build_authorization_url, exchange_code_for_tokens

router = APIRouter(
    prefix="/api/v1/integrations",
    tags=["Integrations"],
    dependencies=[Depends(get_api_key)],
)


# ─── Schemas ─────────────────────────────────────────────────────────


class XeroConnectResponse(BaseModel):
    authorization_url: str
    state: str


class XeroStatusResponse(BaseModel):
    connected: bool
    tenant_id: str | None = None


# ─── Routes ──────────────────────────────────────────────────────────


@router.get("/xero/connect", response_model=XeroConnectResponse)
async def xero_connect(
    organization_id: str = Query("default"),
) -> XeroConnectResponse:
    """Start Xero OAuth2 flow. Returns the authorization URL to redirect to."""
    from app.config import settings

    if not settings.xero_enabled:
        raise HTTPException(status_code=400, detail="Xero integration is disabled")

    if not settings.xero_client_id or not settings.xero_client_secret:
        raise HTTPException(status_code=400, detail="Xero client_id and client_secret must be configured")

    # Generate a random state value for CSRF protection
    # In production, store this in Redis and validate on callback
    state = secrets.token_urlsafe(16)

    auth_url = build_authorization_url(state)
    return XeroConnectResponse(authorization_url=auth_url, state=state)


@router.get("/xero/callback")
async def xero_callback(
    code: str = Query(...),
    state: str = Query(...),
    organization_id: str = Query("default"),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Handle Xero OAuth2 callback. Exchange code for tokens."""
    from app.config import settings

    if not settings.xero_enabled:
        raise HTTPException(status_code=400, detail="Xero integration is disabled")

    # Validate state parameter (in production, compare against stored value in Redis)
    if not state or len(state) < 8:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # Resolve organization_id to UUID
    org_uuid = uuid.UUID(organization_id) if organization_id != "default" else uuid.UUID(int=0)

    success = await exchange_code_for_tokens(db, org_uuid, code)
    if not success:
        raise HTTPException(status_code=502, detail="Failed to exchange Xero authorization code")

    return {"message": "Xero integration connected successfully"}


@router.get("/xero/status", response_model=XeroStatusResponse)
async def xero_status(
    organization_id: str = Query("default"),
    db: AsyncSession = Depends(get_session),
) -> XeroStatusResponse:
    """Check Xero connection status for an organization."""
    org_uuid = uuid.UUID(organization_id) if organization_id != "default" else uuid.UUID(int=0)

    result = await db.execute(
        select(XeroCredential).where(XeroCredential.organization_id == org_uuid)
    )
    credential = result.scalar_one_or_none()

    if not credential:
        return XeroStatusResponse(connected=False)

    return XeroStatusResponse(
        connected=True,
        tenant_id=credential.tenant_id,
    )
