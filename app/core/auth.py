"""API key authentication dependency with rate limiting."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.api_key import ApiKey

logger = logging.getLogger(__name__)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_api_key(
    request: Request,
    api_key: Optional[str] = Depends(API_KEY_HEADER),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Validate API key from X-API-Key header and enforce rate limits.

    Raises HTTP 401 if missing/invalid, HTTP 429 if rate limited.
    """
    if api_key is None:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # Look up key in database
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active == True,  # noqa: E712
        )
    )
    key_record: Optional[ApiKey] = result.scalar_one_or_none()

    if key_record is None:
        raise HTTPException(status_code=401, detail="Invalid or deactivated API key")

    # Rate limiting via Redis
    try:
        from app.core.redis import get_redis

        r = await get_redis()
        minute_bucket = int(time.time()) // 60
        rate_key = f"rate:{key_hash}:{minute_bucket}"

        current = await r.incr(rate_key)
        if current == 1:
            await r.expire(rate_key, 60)

        if current > key_record.rate_limit_per_minute:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {key_record.rate_limit_per_minute} per minute",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Rate limiting unavailable (non-fatal): %s", exc)

    # Update last_used_at on the key record
    from datetime import datetime, timezone

    key_record.last_used_at = datetime.now(timezone.utc)
