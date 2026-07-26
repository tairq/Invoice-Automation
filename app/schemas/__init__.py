"""Pydantic schemas for API request/response validation."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field


# ─── Line Items ───────────────────────────────────────────────────

class LineItemResponse(BaseModel):
    id: uuid.UUID
    line_number: int
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_price: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    net_amount: Optional[Decimal] = None
    gross_amount: Optional[Decimal] = None
    item_code: Optional[str] = None

    model_config = {"from_attributes": True}


# ─── Extracted Data ───────────────────────────────────────────────

class ExtractedDataResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: Optional[str] = None
    invoice_type: Optional[str] = None
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    currency: Optional[str] = None
    language: Optional[str] = None
    payment_terms: Optional[str] = None
    po_number: Optional[str] = None
    notes: Optional[str] = None

    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    vendor_email: Optional[str] = None
    vendor_phone: Optional[str] = None
    vendor_bank_name: Optional[str] = None
    vendor_bank_account: Optional[str] = None
    vendor_bank_iban: Optional[str] = None
    vendor_bank_swift: Optional[str] = None

    customer_name: Optional[str] = None
    customer_address: Optional[str] = None
    customer_tax_id: Optional[str] = None

    subtotal: Optional[Decimal] = None
    tax_total: Optional[Decimal] = None
    discount_total: Optional[Decimal] = None
    grand_total: Optional[Decimal] = None
    amount_due: Optional[Decimal] = None
    amount_paid: Optional[Decimal] = None

    model_config = {"from_attributes": True}


# ─── Invoice ──────────────────────────────────────────────────────

class InvoiceResponse(BaseModel):
    id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    status: str
    source: str
    original_filename: str
    file_type: str
    file_size: int
    confidence_score: Optional[float] = None
    needs_review: bool
    is_duplicate: bool
    error_message: Optional[str] = None
    processed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    extracted_data: Optional[ExtractedDataResponse] = None
    line_items: list[LineItemResponse] = []

    model_config = {"from_attributes": True}


class InvoiceListResponse(BaseModel):
    id: uuid.UUID
    status: str
    source: str
    original_filename: str
    file_type: str
    confidence_score: Optional[float] = None
    needs_review: bool
    is_duplicate: bool
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    grand_total: Optional[Decimal] = None
    currency: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


# ─── Upload ───────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    id: uuid.UUID
    filename: str
    status: str
    message: str = "Invoice uploaded and queued for processing."


# ─── Review ───────────────────────────────────────────────────────

class FieldCorrection(BaseModel):
    field_name: str = Field(..., description="Name of the field being corrected")
    corrected_value: Optional[str] = Field(None, description="Human-provided correct value")
    reviewer_notes: Optional[str] = None


class ReviewRequest(BaseModel):
    corrections: list[FieldCorrection]


class ReviewResponse(BaseModel):
    invoice_id: uuid.UUID
    status: str
    corrections_applied: int


# ─── Stats ────────────────────────────────────────────────────────

class MonthlySpend(BaseModel):
    month: str  # "2025-01"
    total: float
    count: int


class TopVendor(BaseModel):
    name: str
    total: float
    count: int


class DashboardStats(BaseModel):
    total_invoices: int
    processed_today: int
    needs_review: int
    failed: int
    average_confidence: float
    total_amount_processed: Decimal
    invoices_by_status: dict[str, int]
    invoices_by_vendor: list[dict[str, Any]]
    # Enhanced analytics
    total_amount_by_currency: dict[str, float] = {}
    avg_processing_time_seconds: float = 0.0
    top_vendors: list[TopVendor] = []
    monthly_spend: list[MonthlySpend] = []
    anomaly_rate: Optional[float] = None


# ─── Webhook Delivery ─────────────────────────────────────────────

class WebhookDeliveryResponse(BaseModel):
    id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    invoice_id: uuid.UUID
    webhook_url: str
    event_type: str
    attempt_number: int
    status: str
    response_code: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None
    attempted_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── n8n ──────────────────────────────────────────────────────────

class N8nTriggerResponse(BaseModel):
    success: bool
    status_code: int
    response: Optional[str] = None


# ─── Export ───────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    invoice_ids: Optional[list[uuid.UUID]] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    format: str = "csv"  # csv or json


# ─── Email Config ─────────────────────────────────────────────────

class EmailConfigRequest(BaseModel):
    host: str
    port: int = 993
    username: str
    password: str
    check_interval: int = 300
    ssl: bool = True


class EmailConfigResponse(BaseModel):
    host: str
    port: int
    username: str
    check_interval: int
    ssl: bool
    is_configured: bool


# ─── Webhook ──────────────────────────────────────────────────────

class WebhookConfigRequest(BaseModel):
    url: str
    events: list[str] = ["invoice.processed", "invoice.failed"]
    secret: Optional[str] = None


# ─── Processing Log ───────────────────────────────────────────────

class ProcessingLogResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    step: str
    status: str
    message: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}
