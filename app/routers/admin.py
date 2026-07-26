"""Admin API routes for API key management."""
from __future__ import annotations

import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models.api_key import ApiKey

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


# ─── Schemas ─────────────────────────────────────────────────────────


class CreateKeyRequest(BaseModel):
    client_name: str
    rate_limit_per_minute: int = 60


class CreateKeyResponse(BaseModel):
    id: uuid.UUID
    client_name: str
    raw_key: str  # Shown once on creation
    rate_limit_per_minute: int


class KeyResponse(BaseModel):
    id: uuid.UUID
    client_name: str
    is_active: bool
    rate_limit_per_minute: int
    created_at: str

    model_config = {"from_attributes": True}


# ─── Admin Auth Helper ───────────────────────────────────────────────


def verify_admin(admin_key: str = Header(..., alias="X-Admin-Key")) -> None:
    """Verify the admin API key from header."""
    if not admin_key or admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


# ─── Routes ──────────────────────────────────────────────────────────


@router.post("/keys", response_model=CreateKeyResponse, status_code=201)
async def create_api_key(
    request: CreateKeyRequest,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(verify_admin),
) -> CreateKeyResponse:
    """Create a new API key. Returns the raw key once — it will not be shown again."""
    raw_key = f"ip_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    key_record = ApiKey(
        key_hash=key_hash,
        client_name=request.client_name,
        rate_limit_per_minute=request.rate_limit_per_minute,
    )
    db.add(key_record)
    await db.flush()

    return CreateKeyResponse(
        id=key_record.id,
        client_name=key_record.client_name,
        raw_key=raw_key,
        rate_limit_per_minute=key_record.rate_limit_per_minute,
    )


@router.get("/keys", response_model=list[KeyResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_session),
    _: None = Depends(verify_admin),
) -> list[KeyResponse]:
    """List all API keys (without exposing the raw keys)."""
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    keys = result.scalars().all()
    return [KeyResponse.model_validate(k) for k in keys]


@router.patch("/keys/{key_id}/deactivate")
async def deactivate_api_key(
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(verify_admin),
) -> dict:
    """Deactivate an API key."""
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    key_record = result.scalar_one_or_none()
    if not key_record:
        raise HTTPException(status_code=404, detail="Key not found")
    key_record.is_active = False
    return {"message": f"Key '{key_record.client_name}' deactivated"}
