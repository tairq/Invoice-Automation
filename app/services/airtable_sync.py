"""Airtable sync service — push invoice data to Airtable tables."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

AIRTABLE_API_URL = "https://api.airtable.com/v0"


def _fmt(val: Any) -> Any:
    """Format a value for Airtable field storage."""
    if val is None:
        return None
    if isinstance(val, (Decimal, float)):
        return float(val)
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    if isinstance(val, UUID):
        return str(val)
    return val


def _build_invoice_record(
    invoice: dict, extracted_data: Optional[dict], line_items: list[dict]
) -> dict:
    """Build a single Airtable record from invoice + extracted data."""
    ed = extracted_data or {}
    fields: dict[str, Any] = {
        # Identity
        "Invoice ID": str(invoice.get("id", "")),
        "Status": invoice.get("status", ""),
        "Source": invoice.get("source", ""),
        "Filename": invoice.get("original_filename", ""),
        "File Type": invoice.get("file_type", ""),
        # Vendor
        "Vendor Name": _fmt(ed.get("vendor_name")),
        "Vendor Address": _fmt(ed.get("vendor_address")),
        "Vendor Tax ID": _fmt(ed.get("vendor_tax_id")),
        "Vendor Email": _fmt(ed.get("vendor_email")),
        "Vendor Phone": _fmt(ed.get("vendor_phone")),
        "Vendor Bank Name": _fmt(ed.get("vendor_bank_name")),
        "Vendor Bank IBAN": _fmt(ed.get("vendor_bank_iban")),
        "Vendor Bank SWIFT": _fmt(ed.get("vendor_bank_swift")),
        # Customer
        "Customer Name": _fmt(ed.get("customer_name")),
        "Customer Address": _fmt(ed.get("customer_address")),
        "Customer Tax ID": _fmt(ed.get("customer_tax_id")),
        # Invoice details
        "Invoice Number": _fmt(ed.get("invoice_number")),
        "Invoice Type": _fmt(ed.get("invoice_type")),
        "Issue Date": _fmt(ed.get("issue_date")),
        "Due Date": _fmt(ed.get("due_date")),
        "Currency": _fmt(ed.get("currency")),
        "PO Number": _fmt(ed.get("po_number")),
        "Payment Terms": _fmt(ed.get("payment_terms")),
        # Totals
        "Subtotal": _fmt(ed.get("subtotal")),
        "Tax Total": _fmt(ed.get("tax_total")),
        "Discount Total": _fmt(ed.get("discount_total")),
        "Grand Total": _fmt(ed.get("grand_total")),
        "Amount Due": _fmt(ed.get("amount_due")),
        "Amount Paid": _fmt(ed.get("amount_paid")),
        # Processing
        "Confidence Score": _fmt(invoice.get("confidence_score")),
        "Needs Review": invoice.get("needs_review", False),
        "Is Duplicate": invoice.get("is_duplicate", False),
        "Error Message": _fmt(invoice.get("error_message")),
        "Processed At": _fmt(invoice.get("processed_at")),
        "Created At": _fmt(invoice.get("created_at")),
    }

    # Count line items
    fields["Line Items Count"] = len(line_items)

    return {"fields": fields}


def _build_line_item_records(invoice_id: str, line_items: list[dict]) -> list[dict]:
    """Build Airtable records for each line item."""
    records = []
    for item in line_items:
        fields: dict[str, Any] = {
            "Invoice ID": invoice_id,
            "Line Number": item.get("line_number"),
            "Description": _fmt(item.get("description")),
            "Quantity": _fmt(item.get("quantity")),
            "Unit": _fmt(item.get("unit")),
            "Unit Price": _fmt(item.get("unit_price")),
            "Tax Rate": _fmt(item.get("tax_rate")),
            "Tax Amount": _fmt(item.get("tax_amount")),
            "Discount Amount": _fmt(item.get("discount_amount")),
            "Net Amount": _fmt(item.get("net_amount")),
            "Gross Amount": _fmt(item.get("gross_amount")),
            "Item Code": _fmt(item.get("item_code")),
        }
        records.append({"fields": fields})
    return records


def _headers() -> dict[str, str]:
    """Build Airtable API headers."""
    return {
        "Authorization": f"Bearer {settings.airtable_api_key}",
        "Content-Type": "application/json",
    }


def _table_url(table_name: str) -> str:
    """Build Airtable table endpoint URL."""
    base_id = settings.airtable_base_id
    return f"{AIRTABLE_API_URL}/{base_id}/{table_name}"


def _upsert_invoice_record(invoice_record: dict) -> Optional[dict]:
    """Upsert an invoice record by matching on Invoice ID field.

    Airtable doesn't have native upsert, so we query first, then create or update.
    """
    headers = _headers()
    invoice_id = invoice_record["fields"]["Invoice ID"]
    table_url = _table_url(settings.airtable_invoices_table)

    # Try to find existing record by Invoice ID
    try:
        formula = f"{{Invoice ID}} = '{invoice_id}'"
        resp = httpx.get(
            table_url,
            headers=headers,
            params={"filterByFormula": formula, "maxRecords": 1},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", [])
    except Exception as exc:
        logger.warning("Airtable lookup failed for %s: %s", invoice_id, exc)
        return None

    try:
        if records:
            # Update existing record
            record_id = records[0]["id"]
            resp = httpx.patch(
                f"{table_url}/{record_id}",
                headers=headers,
                json={"fields": invoice_record["fields"]},
                timeout=15.0,
            )
            resp.raise_for_status()
            logger.info("Airtable: updated invoice %s (record %s)", invoice_id, record_id)
            return resp.json()
        else:
            # Create new record
            resp = httpx.post(
                table_url,
                headers=headers,
                json={"records": [invoice_record]},
                timeout=15.0,
            )
            resp.raise_for_status()
            created = resp.json()
            logger.info(
                "Airtable: created invoice %s (record %s)",
                invoice_id,
                created["records"][0]["id"],
            )
            return created
    except Exception as exc:
        logger.warning("Airtable write failed for invoice %s: %s", invoice_id, exc)
        return None


def _sync_line_items(invoice_id: str, line_items: list[dict]) -> None:
    """Push line items to the Airtable Line Items table.

    Uses a simple replace strategy: delete existing items for this invoice, then insert new ones.
    """
    table_name = settings.airtable_line_items_table
    if not table_name or not line_items:
        return

    headers = _headers()
    table_url = _table_url(table_name)

    # Delete existing line items for this invoice
    try:
        formula = f"{{Invoice ID}} = '{invoice_id}'"
        while True:
            resp = httpx.get(
                table_url,
                headers=headers,
                params={"filterByFormula": formula, "fields": ["Invoice ID"], "pageSize": 100},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            record_ids = [r["id"] for r in data.get("records", [])]
            if not record_ids:
                break
            # Delete in batches of 10 (Airtable max per request)
            for i in range(0, len(record_ids), 10):
                batch = record_ids[i : i + 10]
                resp = httpx.request(
                    "DELETE",
                    table_url,
                    headers=headers,
                    json={"records": batch},
                    timeout=15.0,
                )
                resp.raise_for_status()
            if not data.get("offset"):
                break
    except Exception as exc:
        logger.warning("Airtable: failed to clear line items for %s: %s", invoice_id, exc)
        return

    # Insert new line items
    try:
        records = _build_line_item_records(invoice_id, line_items)
        # Airtable max 10 records per create request
        for i in range(0, len(records), 10):
            batch = records[i : i + 10]
            resp = httpx.post(
                table_url,
                headers=headers,
                json={"records": batch},
                timeout=15.0,
            )
            resp.raise_for_status()
        logger.info("Airtable: synced %d line items for invoice %s", len(line_items), invoice_id)
    except Exception as exc:
        logger.warning("Airtable: failed to insert line items for %s: %s", invoice_id, exc)


def sync_invoice(invoice: dict) -> None:
    """Push invoice data to Airtable.

    This is the main entry point called after pipeline completion.
    Takes a dict representation of the invoice with extracted_data and line_items.
    """
    if not settings.airtable_sync_enabled:
        return

    if not settings.airtable_api_key or not settings.airtable_base_id:
        logger.warning("Airtable sync enabled but API key or Base ID not configured")
        return

    extracted_data = invoice.get("extracted_data")
    line_items = invoice.get("line_items", [])
    invoice_id = str(invoice["id"])

    # Build and upsert the main invoice record
    invoice_record = _build_invoice_record(invoice, extracted_data, line_items)
    result = _upsert_invoice_record(invoice_record)
    if not result:
        return

    # Sync line items to separate table
    _sync_line_items(invoice_id, line_items)
