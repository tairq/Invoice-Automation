"""Tests for integration routes (Xero)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.config import settings


class TestXeroConnect:
    async def test_connect_returns_url(self, client: AsyncClient):
        with patch.object(settings, "xero_enabled", True), \
             patch.object(settings, "xero_client_id", "test-id"), \
             patch.object(settings, "xero_client_secret", "test-secret"):
            resp = await client.get("/api/v1/integrations/xero/connect")
            assert resp.status_code == 200
            data = resp.json()
            assert "authorization_url" in data
            assert data["authorization_url"].startswith("https://login.xero.com/")

    async def test_status_not_connected(self, client: AsyncClient):
        resp = await client.get("/api/v1/integrations/xero/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False


class TestXeroCallback:
    async def test_callback_missing_code(self, client: AsyncClient):
        resp = await client.get("/api/v1/integrations/xero/callback")
        assert resp.status_code == 422  # Missing required query param

    async def test_callback_success(self, client: AsyncClient):
        with patch.object(settings, "xero_enabled", True), \
             patch.object(settings, "xero_client_id", "test-id"), \
             patch.object(settings, "xero_client_secret", "test-secret"), \
             patch("app.routers.integrations.exchange_code_for_tokens") as mock:
            mock.return_value = True
            resp = await client.get(
                "/api/v1/integrations/xero/callback",
                params={"code": "test-code", "state": "test-state-1234"},
            )
            assert resp.status_code == 200
            assert resp.json()["message"] == "Xero integration connected successfully"
