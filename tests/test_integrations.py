"""Tests for integration routes (Xero)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.config import settings


class TestXeroConnect:
    async def test_connect_returns_url(self, client: AsyncClient):
        with patch.object(settings, "xero_enabled", True), \
             patch.object(settings, "xero_client_id", "test-id"):
            resp = await client.get("/api/v1/integrations/xero/connect")
            assert resp.status_code == 200
            data = resp.json()
            assert "authorization_url" in data
            assert data["authorization_url"].startswith("https://login.xero.com/")
            assert "code_verifier" in data
            assert len(data["code_verifier"]) > 0

    async def test_status_not_connected(self, client: AsyncClient):
        resp = await client.get("/api/v1/integrations/xero/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False


class TestXeroCallback:
    async def test_callback_missing_code(self, client: AsyncClient):
        resp = await client.post("/api/v1/integrations/xero/callback", json={})
        assert resp.status_code == 422  # Missing required field

    async def test_callback_success(self, client: AsyncClient):
        with patch.object(settings, "xero_enabled", True), \
             patch.object(settings, "xero_client_id", "test-id"), \
             patch("app.routers.integrations.exchange_code_for_tokens") as mock:
            mock.return_value = True
            connect = await client.get("/api/v1/integrations/xero/connect")
            connect_data = connect.json()
            resp = await client.post(
                "/api/v1/integrations/xero/callback",
                json={
                    "code": "test-code-from-xero",
                    "code_verifier": connect_data["code_verifier"],
                    "state": connect_data["state"],
                },
            )
            assert resp.status_code == 200
            assert resp.json()["message"] == "Xero integration connected successfully"
            mock.assert_awaited_once()

    async def test_callback_rejects_unknown_state(self, client: AsyncClient):
        with patch.object(settings, "xero_enabled", True):
            resp = await client.post(
                "/api/v1/integrations/xero/callback",
                json={
                    "code": "test-code-from-xero",
                    "code_verifier": "test-verifier",
                    "state": "unknown-state",
                },
            )
            assert resp.status_code == 400
            assert "state" in resp.json()["detail"].lower()
