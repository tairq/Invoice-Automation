"""AI-powered extraction service — the core intelligence."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Optional

from app.core.llm_client import llm_client

logger = logging.getLogger(__name__)

# Expected fields the LLM should return
EXTRACTION_SCHEMA = {
    "invoice_number": "string or null",
    "invoice_type": "string: invoice|credit_note|proforma|receipt|unknown",
    "issue_date": "date YYYY-MM-DD or null",
    "due_date": "date YYYY-MM-DD or null",
    "period_start": "date YYYY-MM-DD or null",
    "period_end": "date YYYY-MM-DD or null",
    "currency": "ISO 4217 code or null",
    "language": "language code or null",
    "payment_terms": "string or null",
    "po_number": "string or null",
    "notes": "string or null",
    "vendor_name": "string or null",
    "vendor_address": "string or null",
    "vendor_tax_id": "string or null",
    "vendor_email": "string or null",
    "vendor_phone": "string or null",
    "vendor_bank_name": "string or null",
    "vendor_bank_account": "string or null",
    "vendor_bank_iban": "string or null",
    "vendor_bank_swift": "string or null",
    "customer_name": "string or null",
    "customer_address": "string or null",
    "customer_tax_id": "string or null",
    "subtotal": "numeric or null",
    "tax_total": "numeric or null",
    "discount_total": "numeric or null",
    "grand_total": "numeric or null",
    "amount_due": "numeric or null",
    "amount_paid": "numeric or null",
    "line_items": [
        {
            "description": "string or null",
            "quantity": "numeric or null",
            "unit": "string or null",
            "unit_price": "numeric or null",
            "tax_rate": "numeric or null",
            "tax_amount": "numeric or null",
            "discount_amount": "numeric or null",
            "net_amount": "numeric or null",
            "gross_amount": "numeric or null",
            "item_code": "string or null",
        }
    ],
    "confidence_scores": {
        "invoice_number": "0.0-1.0",
        "vendor_name": "0.0-1.0",
        "grand_total": "0.0-1.0",
        "line_items": "0.0-1.0",
        "overall": "0.0-1.0",
    },
}


def extract_invoice_data(image_bytes_list: list[bytes]) -> dict[str, Any]:
    """Extract structured invoice data from images using the LLM.

    Returns a dict with extracted_data, line_items, and confidence_scores.
    """
    logger.info("Extracting invoice data from %d image(s)...", len(image_bytes_list))

    raw = llm_client.extract(image_bytes_list)

    logger.debug("Raw LLM extraction result: %s", json.dumps(raw, default=str)[:500])

    # Build structured output
    result = {
        "extracted_data": _extract_header_data(raw),
        "line_items": _extract_line_items(raw),
        "confidence_scores": _extract_confidence_scores(raw),
        "raw_extraction": json.dumps(raw, default=str),
    }

    return result


def _extract_header_data(raw: dict) -> dict:
    """Extract invoice-level fields."""
    return {
        "invoice_number": raw.get("invoice_number"),
        "invoice_type": raw.get("invoice_type"),
        "issue_date": _safe_date(raw.get("issue_date")),
        "due_date": _safe_date(raw.get("due_date")),
        "period_start": _safe_date(raw.get("period_start")),
        "period_end": _safe_date(raw.get("period_end")),
        "currency": raw.get("currency"),
        "language": raw.get("language"),
        "payment_terms": raw.get("payment_terms"),
        "po_number": raw.get("po_number"),
        "notes": _safe_string(raw.get("notes")),
        "vendor_name": _safe_string(raw.get("vendor_name")),
        "vendor_address": _safe_string(raw.get("vendor_address")),
        "vendor_tax_id": _safe_string(raw.get("vendor_tax_id")),
        "vendor_email": _safe_string(raw.get("vendor_email")),
        "vendor_phone": _safe_string(raw.get("vendor_phone")),
        "vendor_bank_name": _safe_string(raw.get("vendor_bank_name")),
        "vendor_bank_account": _safe_string(raw.get("vendor_bank_account")),
        "vendor_bank_iban": _safe_string(raw.get("vendor_bank_iban")),
        "vendor_bank_swift": _safe_string(raw.get("vendor_bank_swift")),
        "customer_name": _safe_string(raw.get("customer_name")),
        "customer_address": _safe_string(raw.get("customer_address")),
        "customer_tax_id": _safe_string(raw.get("customer_tax_id")),
        "subtotal": _safe_decimal(raw.get("subtotal")),
        "tax_total": _safe_decimal(raw.get("tax_total")),
        "discount_total": _safe_decimal(raw.get("discount_total")),
        "grand_total": _safe_decimal(raw.get("grand_total")),
        "amount_due": _safe_decimal(raw.get("amount_due")),
        "amount_paid": _safe_decimal(raw.get("amount_paid")),
    }


def _extract_line_items(raw: dict) -> list[dict]:
    """Extract line items from raw LLM output."""
    items = raw.get("line_items", [])
    if not isinstance(items, list):
        return []

    result = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "line_number": i + 1,
                "description": item.get("description"),
                "quantity": _safe_decimal(item.get("quantity")),
                "unit": item.get("unit"),
                "unit_price": _safe_decimal(item.get("unit_price")),
                "tax_rate": _safe_decimal(item.get("tax_rate")),
                "tax_amount": _safe_decimal(item.get("tax_amount")),
                "discount_amount": _safe_decimal(item.get("discount_amount")),
                "net_amount": _safe_decimal(item.get("net_amount")),
                "gross_amount": _safe_decimal(item.get("gross_amount")),
                "item_code": item.get("item_code"),
            }
        )
    return result


def _extract_confidence_scores(raw: dict) -> list[dict]:
    """Extract per-field confidence scores."""
    conf = raw.get("confidence_scores", {})
    if not isinstance(conf, dict):
        return []

    return [
        {"field_name": field, "confidence": float(value), "method": "llm"}
        for field, value in conf.items()
        if isinstance(value, (int, float))
    ]


def _safe_decimal(value: Any) -> Optional[float]:
    """Safely convert a value to float (will be stored as Decimal)."""
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (ValueError, TypeError):
        return None


def _safe_date(value: Any) -> Optional[date]:
    """Convert a date value to a Python date object, or return None.

    Handles date objects, datetime objects, and YYYY-MM-DD strings.
    SQLite Date type requires Python date objects.
    """
    from datetime import date, datetime

    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            return None
    return None


def _safe_string(value: Any) -> Optional[str]:
    """Convert a value to a string, joining lists if needed."""
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(v) for v in value if v is not None]
        return "; ".join(parts) if parts else None
    if not isinstance(value, str):
        return str(value)
    return value.strip() or None
