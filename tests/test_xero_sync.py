"""Tests for Xero sync service."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.extracted_data import ExtractedData
from app.models.invoice import Invoice, InvoiceStatus
from app.models.line_item import LineItem
from app.models.xero_credential import XeroCredential
from app.services.xero_client import XeroClient, build_invoice_model
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


class TestBuildInvoiceModel:
    """Tests for the SDK model builder (module-level function)."""

    def test_basic_sdk_model(self):
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
                description="Widget",
                quantity=Decimal("2"),
                unit_price=Decimal("50.00"),
                tax_amount=Decimal("5.00"),
            )
        ]

        sdk_invoice = build_invoice_model(invoice)
        assert sdk_invoice.type == "ACCREC"
        assert sdk_invoice.contact.name == "Acme Corp"
        assert sdk_invoice.reference == "INV-001"
        assert sdk_invoice.currency_code == "USD"
        assert sdk_invoice.status == "AUTHORISED"
        assert len(sdk_invoice.line_items) == 1
        assert sdk_invoice.line_items[0].description == "Widget"
        assert sdk_invoice.line_items[0].quantity == 2.0
        assert sdk_invoice.line_items[0].unit_amount == 50.0
        assert sdk_invoice.line_items[0].tax_amount == 5.0
        assert sdk_invoice.sub_total == 100.0
        assert sdk_invoice.total_tax == 10.0
        assert sdk_invoice.total == 110.0

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
            build_invoice_model(invoice)

    def test_empty_line_items_fallback(self):
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
        )

        sdk_invoice = build_invoice_model(invoice)
        assert len(sdk_invoice.line_items) == 1  # Default fallback
        assert sdk_invoice.line_items[0].description == "Services"


class TestXeroClient:
    """Tests for the synchronous XeroClient wrapper."""

    def test_init_requires_token(self):
        cred = XeroCredential(
            organization_id=uuid.uuid4(),
            access_token="",  # Empty token should fail
            refresh_token="refresh",
            token_expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
        with pytest.raises(ValueError, match="no access_token"):
            XeroClient(cred)

    def test_push_invoice_no_tenant(self):
        cred = XeroCredential(
            organization_id=uuid.uuid4(),
            access_token="valid-token",
            refresh_token="refresh",
            token_expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            tenant_id=None,
        )
        client = XeroClient(cred)
        invoice = Invoice(
            id=uuid.uuid4(),
            status=InvoiceStatus.done,
            original_filename="test.pdf",
            file_path="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        result = client.push_invoice(invoice)
        assert result is None  # No tenant configured

    def test_push_invoice_no_extracted_data(self):
        cred = XeroCredential(
            organization_id=uuid.uuid4(),
            access_token="valid-token",
            refresh_token="refresh",
            token_expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            tenant_id="tenant-123",
        )
        client = XeroClient(cred)
        invoice = Invoice(
            id=uuid.uuid4(),
            status=InvoiceStatus.done,
            original_filename="test.pdf",
            file_path="test.pdf",
            file_type="pdf",
            file_size=100,
        )
        result = client.push_invoice(invoice)
        assert result is None  # No extracted data = logged and skipped

    @patch("app.services.xero_client.AccountingApi")
    def test_push_invoice_success(self, mock_accounting_api_class):
        """Verify push_invoice calls create_invoices and returns the InvoiceID."""
        # Arrange
        fake_invoice_id = "xero-inv-123"
        mock_api = MagicMock()
        mock_accounting_api_class.return_value = mock_api

        from xero_python.accounting import Invoice as SdkInvoice, Invoices

        mock_api.create_invoices.return_value = Invoices(
            invoices=[SdkInvoice(invoice_id=fake_invoice_id)]
        )

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
        )

        cred = XeroCredential(
            organization_id=uuid.uuid4(),
            access_token="valid-token",
            refresh_token="refresh",
            token_expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            tenant_id="tenant-123",
        )
        client = XeroClient(cred)

        # Act
        result = client.push_invoice(invoice)

        # Assert
        assert result == fake_invoice_id
        mock_api.create_invoices.assert_called_once()
        # Verify the call contained our invoice
        call_args = mock_api.create_invoices.call_args[1]
        assert call_args["xero_tenant_id"] == "tenant-123"