"""Tests for API key authentication."""
from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.api_key import ApiKey


@pytest_asyncio.fixture
async def auth_client(db_session):
    """Client with real auth (no bypass override)."""
    from app.core.auth import get_api_key as _get_api_key

    # Remove the bypass override for auth tests
    app.dependency_overrides.pop(_get_api_key, None)

    with patch("app.routers.invoices.process_invoice_task.delay") as mock_task:
        mock_task.return_value = None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    # Restore bypass
    async def _bypass():
        pass
    app.dependency_overrides[_get_api_key] = _bypass


class TestAPIAuth:
    async def test_missing_header(self, auth_client: AsyncClient):
        resp = await auth_client.get("/api/v1/invoices")
        assert resp.status_code == 401
        assert "Missing" in resp.json()["detail"]

    async def test_invalid_key(self, auth_client: AsyncClient):
        resp = await auth_client.get(
            "/api/v1/invoices",
            headers={"X-API-Key": "invalid-key"},
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    async def test_valid_key(
        self, auth_client: AsyncClient, db_session: AsyncSession,
    ):
        raw_key = "test-valid-key-123"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key = ApiKey(
            key_hash=key_hash,
            client_name="test-client",
            rate_limit_per_minute=1000,
        )
        db_session.add(key)
        await db_session.commit()

        resp = await auth_client.get(
            "/api/v1/invoices",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200

    async def test_deactivated_key(
        self, auth_client: AsyncClient, db_session: AsyncSession,
    ):
        raw_key = "deactivated-key"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key = ApiKey(
            key_hash=key_hash,
            client_name="deactivated",
            is_active=False,
        )
        db_session.add(key)
        await db_session.commit()

        resp = await auth_client.get(
            "/api/v1/invoices",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 401

    async def test_health_no_auth(self, auth_client: AsyncClient):
        """Health endpoint should not require auth."""
        resp = await auth_client.get("/health")
        assert resp.status_code == 200

    async def test_exports_require_auth(self, auth_client: AsyncClient):
        """Export endpoints also require auth."""
        resp = await auth_client.get("/api/v1/exports/csv")
        assert resp.status_code == 401
