"""Validation service — cross-field checks, confidence scoring, duplicate detection."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)


def validate_extraction(data: dict, line_items: list[dict]) -> dict[str, Any]:
    """Run validation checks on extracted data.

    Returns dict with:
    - overall_confidence: float 0.0-1.0
    - needs_review: bool
    - validation_errors: list[str]
    - validation_warnings: list[str]
    """
    errors: list[str] = []
    warnings: list[str] = []
    confidences: list[float] = []

    # 1. Check that we got something
    if not data.get("vendor_name") and not data.get("invoice_number"):
        errors.append("No vendor name or invoice number extracted — document may not be an invoice")
        confidences.append(0.1)
    else:
        confidences.append(0.9 if data.get("vendor_name") else 0.3)
        confidences.append(0.9 if data.get("invoice_number") else 0.3)

    # 2. Math cross-check: grand_total ≈ subtotal + tax_total - discount_total
    grand_total = _to_decimal(data.get("grand_total"))
    subtotal = _to_decimal(data.get("subtotal"))
    tax_total = _to_decimal(data.get("tax_total"))
    discount_total = _to_decimal(data.get("discount_total"))

    if grand_total is not None and subtotal is not None:
        expected = subtotal
        if tax_total is not None:
            expected += tax_total
        if discount_total is not None:
            expected -= discount_total

        diff = abs(grand_total - expected)
        if diff > Decimal("0.05"):
            warnings.append(
                f"Grand total ({grand_total}) differs from calculated total ({expected}) by {diff}"
            )
            confidences.append(0.5)
        else:
            confidences.append(1.0)

    # 3. Check for required fields
    required_fields = [
        ("vendor_name", "Vendor name"),
        ("invoice_number", "Invoice number"),
        ("grand_total", "Grand total"),
    ]
    found_required = 0
    for field, label in required_fields:
        if data.get(field):
            found_required += 1
    field_confidence = found_required / len(required_fields)
    confidences.append(field_confidence)

    if found_required < 2:
        warnings.append(f"Only {found_required}/3 required fields found")

    # 4. Line item validation
    if line_items:
        items_total = sum(
            _to_decimal(item.get("net_amount", 0)) or Decimal("0") for item in line_items
        )
        if subtotal is not None and items_total > 0:
            diff = abs(subtotal - items_total)
            if diff > Decimal("1.00"):
                warnings.append(
                    f"Line items total ({items_total}) differs from subtotal ({subtotal})"
                )
                confidences.append(0.6)
            else:
                confidences.append(1.0)
        confidences.append(min(1.0, len(line_items) * 0.15))
    else:
        warnings.append("No line items extracted")
        confidences.append(0.2)

    # 5. Date validation
    if data.get("issue_date") and data.get("due_date"):
        try:
            issue = data["issue_date"]
            due = data["due_date"]
            if isinstance(issue, str):
                from datetime import datetime

                issue = datetime.strptime(issue, "%Y-%m-%d").date()
            if isinstance(due, str):
                from datetime import datetime

                due = datetime.strptime(due, "%Y-%m-%d").date()
            if due < issue:
                warnings.append(f"Due date ({due}) is before issue date ({issue})")
                confidences.append(0.4)
            else:
                confidences.append(1.0)
        except (ValueError, TypeError):
            pass

    # Calculate overall confidence
    overall = sum(confidences) / len(confidences) if confidences else 0.5
    overall = max(0.0, min(1.0, overall))

    needs_review = overall < settings.confidence_threshold or len(errors) > 0

    return {
        "overall_confidence": round(overall, 4),
        "needs_review": needs_review,
        "validation_errors": errors,
        "validation_warnings": warnings,
    }


def _to_decimal(value: Any) -> Optional[Decimal]:
    """Safely convert to Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None
