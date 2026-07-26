"""Tests for the validation service."""
from __future__ import annotations

from app.services.validator import validate_extraction


class TestValidateExtraction:
    def test_perfect_data(self):
        data = {
            "vendor_name": "ACME Corp",
            "invoice_number": "INV-001",
            "issue_date": "2024-01-15",
            "due_date": "2024-02-14",
            "currency": "USD",
            "subtotal": 1000.00,
            "tax_total": 100.00,
            "discount_total": 50.00,
            "grand_total": 1050.00,
        }
        line_items = [
            {"description": "Widget", "net_amount": 500.00},
            {"description": "Gadget", "net_amount": 500.00},
        ]

        result = validate_extraction(data, line_items)
        assert result["overall_confidence"] > 0.7
        assert result["needs_review"] is False

    def test_missing_fields(self):
        data = {
            "vendor_name": None,
            "invoice_number": None,
            "grand_total": None,
        }
        result = validate_extraction(data, [])
        assert result["overall_confidence"] < 0.5
        assert result["needs_review"] is True

    def test_math_mismatch(self):
        data = {
            "vendor_name": "Test Co",
            "invoice_number": "001",
            "subtotal": 100.00,
            "tax_total": 10.00,
            "grand_total": 200.00,  # Should be ~110
        }
        result = validate_extraction(data, [])
        assert len(result["validation_warnings"]) > 0
        assert any("differs" in w for w in result["validation_warnings"])
