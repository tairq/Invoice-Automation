"""Tests for the FastAPI invoice routes."""
from __future__ import annotations

from httpx import AsyncClient


class TestUploadAPI:
    async def test_upload_pdf(self, client: AsyncClient, sample_pdf_bytes: bytes):
        resp = await client.post(
            "/api/v1/invoices/upload",
            files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "test.pdf"
        assert data["status"] == "pending"
        assert "id" in data

    async def test_upload_invalid_type(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/invoices/upload",
            files={"file": ("test.exe", b"content", "application/x-msdownload")},
        )
        assert resp.status_code == 400  # IngestionError caught by handler

    async def test_upload_batch(self, client: AsyncClient, sample_pdf_bytes: bytes):
        files = [
            ("files", ("a.pdf", sample_pdf_bytes, "application/pdf")),
            ("files", ("b.pdf", sample_pdf_bytes, "application/pdf")),
        ]
        resp = await client.post("/api/v1/invoices/upload/batch", files=files)
        assert resp.status_code == 201
        data = resp.json()
        assert len(data) == 2


class TestListInvoicesAPI:
    async def test_empty_list(self, client: AsyncClient):
        resp = await client.get("/api/v1/invoices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_with_invoice(self, client: AsyncClient, sample_pdf_bytes: bytes):
        # Upload first
        await client.post(
            "/api/v1/invoices/upload",
            files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        )

        resp = await client.get("/api/v1/invoices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["original_filename"] == "test.pdf"


class TestInvoiceDetailAPI:
    async def test_get_nonexistent(self, client: AsyncClient):
        resp = await client.get("/api/v1/invoices/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_get_existing(self, client: AsyncClient, sample_pdf_bytes: bytes):
        upload = await client.post(
            "/api/v1/invoices/upload",
            files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")},
        )
        inv_id = upload.json()["id"]

        resp = await client.get(f"/api/v1/invoices/{inv_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == inv_id
        assert data["original_filename"] == "test.pdf"


class TestHealthEndpoint:
    async def test_health_check(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "app" in data
