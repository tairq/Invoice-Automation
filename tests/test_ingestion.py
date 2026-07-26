"""Tests for the ingestion service."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceSource, InvoiceStatus
from app.services.ingestion import IngestionError, create_invoice_record, validate_file


class TestValidateFile:
    def test_valid_pdf(self):
        ext = validate_file("invoice.pdf", b"test content")
        assert ext == "pdf"

    def test_valid_jpg(self):
        ext = validate_file("scan.jpg", b"test content")
        assert ext == "jpg"

    def test_invalid_extension(self):
        with pytest.raises(IngestionError):
            validate_file("invoice.exe", b"test content")

    def test_too_large(self, monkeypatch):
        monkeypatch.setattr("app.services.ingestion.settings.max_file_size_mb", 0.00001)
        with pytest.raises(IngestionError):
            validate_file("invoice.pdf", b"a" * 100)


class TestCreateInvoiceRecord:
    async def test_creates_record(self, db_session: AsyncSession):
        invoice = await create_invoice_record(
            db=db_session,
            filename="test_invoice.pdf",
            content=b"%PDF-1.4 test content",
        )

        assert invoice.id is not None
        assert invoice.original_filename == "test_invoice.pdf"
        assert invoice.file_type == "pdf"
        assert invoice.file_size == 21
        assert invoice.status == InvoiceStatus.pending
        assert invoice.source == InvoiceSource.upload

    async def test_stores_file(self, db_session: AsyncSession):
        content = b"fake pdf content here"
        invoice = await create_invoice_record(
            db=db_session,
            filename="invoice.pdf",
            content=content,
        )

        assert invoice.file_path is not None
        assert "invoices/" in invoice.file_path
        assert invoice.file_path.endswith(".pdf")

    async def test_rejects_invalid_file(self, db_session: AsyncSession):
        with pytest.raises(IngestionError):
            await create_invoice_record(
                db=db_session,
                filename="malware.exe",
                content=b"bad stuff",
            )
