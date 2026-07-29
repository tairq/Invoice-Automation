"""Redis connection pool for rate limiting and caching."""

from __future__ import annotations

import logging
from typing import Optional

from redis.asyncio import ConnectionPool, Redis

from app.config import settings

logger = logging.getLogger(__name__)

_pool: Optional[ConnectionPool] = None


async def get_redis() -> Redis:
    """Return a Redis client from the shared connection pool."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(settings.redis_url, decode_responses=True)
    return Redis(connection_pool=_pool)


async def close_redis() -> None:
    """Dispose of the Redis connection pool."""
    global _pool
    if _pool:
        await _pool.disconnect()
        _pool = None
