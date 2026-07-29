"""Integration API routes — Xero OAuth PKCE flow (Desktop app) + tenant selection."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_api_key
from app.database import get_session
from app.models.xero_credential import XeroCredential
from app.services.xero_sync import (
    build_authorization_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
    resolve_tenants,
)

logger = logging.getLogger(__name__)

_OAUTH_STATE_TTL = timedelta(minutes=10)
_oauth_states: dict[str, tuple[str, str, datetime]] = {}


def _remember_oauth_state(state: str, organization_id: str, code_verifier: str) -> None:
    now = datetime.now(timezone.utc)
    for key, (_, _, created_at) in list(_oauth_states.items()):
        if now - created_at > _OAUTH_STATE_TTL:
            _oauth_states.pop(key, None)
    _oauth_states[state] = (organization_id, code_verifier, now)


def _consume_oauth_state(state: str, organization_id: str, code_verifier: str) -> bool:
    record = _oauth_states.pop(state, None)
    if record is None:
        return False
    stored_org, stored_verifier, created_at = record
    return (
        stored_org == organization_id
        and secrets.compare_digest(stored_verifier, code_verifier)
        and datetime.now(timezone.utc) - created_at <= _OAUTH_STATE_TTL
    )


router = APIRouter(
    prefix="/api/v1/integrations",
    tags=["Integrations"],
    dependencies=[Depends(get_api_key)],
)


class XeroConnectResponse(BaseModel):
    authorization_url: str
    state: str
    code_verifier: str


class XeroStatusResponse(BaseModel):
    connected: bool
    tenant_id: str | None = None
    tenant_name: str | None = None


class XeroTenantInfo(BaseModel):
    tenant_id: str
    tenant_name: str
    tenant_type: str | None = None
    is_active: bool = False


class XeroTenantSelectRequest(BaseModel):
    tenant_id: str


@router.get("/xero/connect", response_model=XeroConnectResponse)
async def xero_connect(
    organization_id: str = Query("default"),
) -> XeroConnectResponse:
    """Start Xero OAuth2 PKCE flow."""
    from app.config import settings

    if not settings.xero_enabled:
        raise HTTPException(status_code=400, detail="Xero integration is disabled")
    if not settings.xero_client_id:
        raise HTTPException(status_code=400, detail="Xero client_id must be configured")

    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    _remember_oauth_state(state, organization_id, code_verifier)
    return XeroConnectResponse(
        authorization_url=build_authorization_url(state, code_challenge),
        state=state,
        code_verifier=code_verifier,
    )


class XeroSubmitCode(BaseModel):
    code: str
    code_verifier: str
    state: str
    organization_id: str = "default"


@router.post("/xero/callback")
async def xero_callback(
    body: XeroSubmitCode,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Submit the Xero authorization code and verify the OAuth state."""
    from app.config import settings

    if not settings.xero_enabled:
        raise HTTPException(status_code=400, detail="Xero integration is disabled")
    if not _consume_oauth_state(body.state, body.organization_id, body.code_verifier):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    org_uuid = (
        uuid.UUID(body.organization_id) if body.organization_id != "default" else uuid.UUID(int=0)
    )
    if not await exchange_code_for_tokens(db, org_uuid, body.code, body.code_verifier):
        raise HTTPException(status_code=502, detail="Failed to exchange Xero authorization code")
    return {"message": "Xero integration connected successfully"}


async def _get_credential(organization_id: str, db: AsyncSession) -> XeroCredential | None:
    org_uuid = uuid.UUID(organization_id) if organization_id != "default" else uuid.UUID(int=0)
    result = await db.execute(
        select(XeroCredential).where(XeroCredential.organization_id == org_uuid)
    )
    return result.scalar_one_or_none()


@router.get("/xero/status", response_model=XeroStatusResponse)
async def xero_status(
    organization_id: str = Query("default"),
    db: AsyncSession = Depends(get_session),
) -> XeroStatusResponse:
    """Check Xero connection status without returning tokens."""
    credential = await _get_credential(organization_id, db)
    if not credential:
        return XeroStatusResponse(connected=False)
    return XeroStatusResponse(
        connected=True,
        tenant_id=credential.tenant_id,
        tenant_name=credential.tenant_name,
    )


@router.get("/xero/tenants", response_model=list[XeroTenantInfo])
async def xero_list_tenants(
    organization_id: str = Query("default"),
    db: AsyncSession = Depends(get_session),
) -> list[XeroTenantInfo]:
    """List authorised Xero organisations for the selected organization."""
    credential = await _get_credential(organization_id, db)
    if not credential or not credential.access_token:
        raise HTTPException(status_code=400, detail="Xero not connected")
    tenants = await resolve_tenants(credential.access_token)
    if not tenants:
        raise HTTPException(
            status_code=502, detail="Failed to list Xero tenants — token may be expired"
        )
    return [
        XeroTenantInfo(
            tenant_id=t.get("tenantId", ""),
            tenant_name=t.get("tenantName", ""),
            tenant_type=t.get("tenantType"),
            is_active=t.get("tenantId") == credential.tenant_id,
        )
        for t in tenants
    ]


@router.post("/xero/tenants/select")
async def xero_select_tenant(
    body: XeroTenantSelectRequest,
    organization_id: str = Query("default"),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Switch the active Xero tenant after validating authorisation."""
    credential = await _get_credential(organization_id, db)
    if not credential or not credential.access_token:
        raise HTTPException(status_code=400, detail="Xero not connected")
    tenants = await resolve_tenants(credential.access_token)
    if not tenants:
        raise HTTPException(
            status_code=502, detail="Failed to list Xero tenants — token may be expired"
        )
    tenant = next((t for t in tenants if t.get("tenantId") == body.tenant_id), None)
    if not tenant:
        raise HTTPException(
            status_code=404, detail="Requested tenant is not an authorised connection"
        )

    credential.tenant_id = tenant["tenantId"]
    credential.tenant_name = tenant.get("tenantName")
    await db.flush()
    logger.info("Switched Xero tenant for organization %s", organization_id)
    return {
        "message": f"Switched to Xero tenant: {credential.tenant_name}",
        "tenant_id": credential.tenant_id,
        "tenant_name": credential.tenant_name,
    }
