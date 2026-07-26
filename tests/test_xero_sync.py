"""Tests for Xero sync service."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.extracted_data import ExtractedData
from app.models.invoice import Invoice, InvoiceStatus
from app.models.line_item import LineItem
from app.services.xero_sync import _build_xero_invoice


class TestBuildXeroInvoice:
    def test_basic_mapping(self):
        invoice_id = uuid.uuid4()
        invoice = Invoice(
            id=invoice_id,
            status=InvoiceStatus.done,
            original_filename="test.pdf",
            file_path="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        invoice.extracted_data = ExtractedData(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            vendor_name="Acme Corp",
            invoice_number="INV-001",
            issue_date=date(2025, 1, 15),
            due_date=date(2025, 2, 15),
            currency="USD",
            subtotal=Decimal("100.00"),
            tax_total=Decimal("10.00"),
            grand_total=Decimal("110.00"),
        )
        invoice.line_items = [
            LineItem(
                id=uuid.uuid4(),
                invoice_id=invoice_id,
                line_number=1,
                description="Widget",
                quantity=Decimal("2"),
                unit_price=Decimal("50.00"),
            )
        ]

        payload = _build_xero_invoice(invoice)
        assert payload["Type"] == "ACCREC"
        assert payload["Contact"]["Name"] == "Acme Corp"
        assert payload["Reference"] == "INV-001"
        assert payload["CurrencyCode"] == "USD"
        assert len(payload["LineItems"]) == 1
        assert payload["LineItems"][0]["Description"] == "Widget"
        assert payload["LineItems"][0]["Quantity"] == 2.0

    def test_no_extracted_data_raises(self):
        invoice = Invoice(
            id=uuid.uuid4(),
            status=InvoiceStatus.done,
            original_filename="test.pdf",
            file_path="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        with pytest.raises(ValueError, match="no extracted data"):
            _build_xero_invoice(invoice)

    def test_empty_line_items(self):
        invoice_id = uuid.uuid4()
        invoice = Invoice(
            id=invoice_id,
            status=InvoiceStatus.done,
            original_filename="test.pdf",
            file_path="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        invoice.extracted_data = ExtractedData(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            vendor_name="Vendor Inc",
            invoice_number="INV-002",
            currency="EUR",
            grand_total=Decimal("200.00"),
        )
        invoice.line_items = []

        payload = _build_xero_invoice(invoice)
        assert len(payload["LineItems"]) == 1  # Default fallback
        assert payload["Contact"]["Name"] == "Vendor Inc"
