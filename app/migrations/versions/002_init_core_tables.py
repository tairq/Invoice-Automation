"""Create all core tables (organizations, invoices, vendors, POs, etc.)

This migration creates the core domain tables that were previously only
created by app.database.init_db() via Base.metadata.create_all(). Without
this migration, subsequent migrations (e.g. adding Xero tables with FK
references to organizations) fail on a fresh database.

Revision ID: 002
Revises: 001
Create Date: 2026-07-29 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 0. Create enum types explicitly before any columns reference them
    #    (columns below use create_type=False to avoid duplicate-type
    #     errors when init_db() calls Base.metadata.create_all() at runtime)
    # ---------------------------------------------------------------
    op.execute("CREATE TYPE IF NOT EXISTS invoice_status AS ENUM ('pending', 'processing', 'done', 'failed', 'needs_review')")
    op.execute("CREATE TYPE IF NOT EXISTS approval_status AS ENUM ('pending_approval', 'approved', 'rejected', 'auto_approved')")
    op.execute("CREATE TYPE IF NOT EXISTS po_match_status AS ENUM ('matched', 'partial', 'unmatched', 'discrepancy')")

    # ---------------------------------------------------------------
    # 1. organizations
    # ---------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email_config", sa.JSON(), nullable=True),
        sa.Column("webhook_url", sa.Text(), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---------------------------------------------------------------
    # 2. vendors
    # ---------------------------------------------------------------
    op.create_table(
        "vendors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("aliases", sa.Text(), nullable=True),
        sa.Column("tax_id", sa.String(100), nullable=True),
        sa.Column("payment_terms", sa.String(255), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_name"),
    )
    op.create_index(op.f("ix_vendors_canonical_name"), "vendors", ["canonical_name"])

    # ---------------------------------------------------------------
    # 3. purchase_orders
    # ---------------------------------------------------------------
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("po_number", sa.String(255), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("line_items", sa.Text(), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default=sa.text("'USD'")),
        sa.Column(
            "status",
            sa.Enum("open", "fulfilled", "cancelled", name="po_status"),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("po_number"),
    )
    op.create_index(op.f("ix_purchase_orders_po_number"), "purchase_orders", ["po_number"])

    # ---------------------------------------------------------------
    # 4. invoices (xero_invoice_id intentionally omitted — added in 003)
    # ---------------------------------------------------------------
    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "done",
                "failed",
                "needs_review",
                name="invoice_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
            index=True,
        ),
        sa.Column(
            "source",
            sa.Enum("upload", "email", "folder", "api", name="invoice_source"),
            nullable=False,
            server_default=sa.text("'upload'"),
        ),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            index=True,
        ),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "approval_status",
            sa.Enum(
                "pending_approval",
                "approved",
                "rejected",
                "auto_approved",
                name="approval_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending_approval'"),
            index=True,
        ),
        sa.Column(
            "matched_po_id",
            sa.Uuid(),
            sa.ForeignKey("purchase_orders.id"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "po_match_status",
            sa.Enum(
                "matched",
                "partial",
                "unmatched",
                "discrepancy",
                name="po_match_status",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("vendor_id", sa.Uuid(), sa.ForeignKey("vendors.id"), nullable=True, index=True),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True, index=True),
        sa.Column(
            "payment_status",
            sa.Enum("unpaid", "paid", "overdue", name="payment_status"),
            nullable=True,
            server_default=sa.text("'unpaid'"),
            index=True,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---------------------------------------------------------------
    # 5. extracted_data
    # ---------------------------------------------------------------
    op.create_table(
        "extracted_data",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id"), nullable=False, unique=True
        ),
        sa.Column("invoice_number", sa.String(255), nullable=True, index=True),
        sa.Column("invoice_type", sa.String(50), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("payment_terms", sa.String(255), nullable=True),
        sa.Column("po_number", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("vendor_name", sa.String(255), nullable=True, index=True),
        sa.Column("vendor_address", sa.Text(), nullable=True),
        sa.Column("vendor_tax_id", sa.String(100), nullable=True),
        sa.Column("vendor_email", sa.String(255), nullable=True),
        sa.Column("vendor_phone", sa.String(50), nullable=True),
        sa.Column("vendor_bank_name", sa.String(255), nullable=True),
        sa.Column("vendor_bank_account", sa.String(100), nullable=True),
        sa.Column("vendor_bank_iban", sa.String(50), nullable=True),
        sa.Column("vendor_bank_swift", sa.String(20), nullable=True),
        sa.Column("vendor_verified", sa.Boolean(), nullable=True),
        sa.Column("vendor_id", sa.Uuid(), sa.ForeignKey("vendors.id"), nullable=True, index=True),
        sa.Column("customer_name", sa.String(255), nullable=True),
        sa.Column("customer_address", sa.Text(), nullable=True),
        sa.Column("customer_tax_id", sa.String(100), nullable=True),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=True),
        sa.Column("tax_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("discount_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("grand_total", sa.Numeric(12, 2), nullable=True, index=True),
        sa.Column("amount_due", sa.Numeric(12, 2), nullable=True),
        sa.Column("amount_paid", sa.Numeric(12, 2), nullable=True),
        sa.Column("raw_extraction_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_extracted_data_invoice_id"), "extracted_data", ["invoice_id"])
    op.create_index(op.f("ix_extracted_data_vendor_name"), "extracted_data", ["vendor_name"])
    op.create_index(op.f("ix_extracted_data_grand_total"), "extracted_data", ["grand_total"])
    op.create_index(op.f("ix_extracted_data_vendor_id"), "extracted_data", ["vendor_id"])
    op.create_index(op.f("ix_extracted_data_invoice_number"), "extracted_data", ["invoice_number"])

    # ---------------------------------------------------------------
    # 6. line_items
    # ---------------------------------------------------------------
    op.create_table(
        "line_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("gross_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("item_code", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_line_items_invoice_id"), "line_items", ["invoice_id"])

    # ---------------------------------------------------------------
    # 7. extraction_confidence
    # ---------------------------------------------------------------
    op.create_table(
        "extraction_confidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("method", sa.String(20), nullable=False, server_default=sa.text("'llm'")),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("corrected_value", sa.Text(), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_extraction_confidence_invoice_id"), "extraction_confidence", ["invoice_id"]
    )

    # ---------------------------------------------------------------
    # 8. processing_logs
    # ---------------------------------------------------------------
    op.create_table(
        "processing_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("step", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_processing_logs_invoice_id"), "processing_logs", ["invoice_id"])

    # ---------------------------------------------------------------
    # 9. approval_tokens
    # ---------------------------------------------------------------
    op.create_table(
        "approval_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("token", sa.String(36), nullable=False),
        sa.Column("approver_email", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index(op.f("ix_approval_tokens_invoice_id"), "approval_tokens", ["invoice_id"])
    op.create_index(op.f("ix_approval_tokens_token"), "approval_tokens", ["token"])

    # ---------------------------------------------------------------
    # 10. po_matches
    # ---------------------------------------------------------------
    op.create_table(
        "po_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("po_id", sa.Uuid(), sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("discrepancies", sa.Text(), nullable=True),
        sa.Column(
            "matched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_po_matches_invoice_id"), "po_matches", ["invoice_id"])
    op.create_index(op.f("ix_po_matches_po_id"), "po_matches", ["po_id"])

    # ---------------------------------------------------------------
    # 11. webhook_deliveries
    # ---------------------------------------------------------------
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("webhook_url", sa.String(1024), nullable=False),
        sa.Column(
            "event_type",
            sa.String(100),
            nullable=False,
            server_default=sa.text("'invoice.processed'"),
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default=sa.text("'pending'"), index=True
        ),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_webhook_deliveries_organization_id"), "webhook_deliveries", ["organization_id"]
    )
    op.create_index(op.f("ix_webhook_deliveries_invoice_id"), "webhook_deliveries", ["invoice_id"])
    op.create_index(op.f("ix_webhook_deliveries_status"), "webhook_deliveries", ["status"])


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("webhook_deliveries")
    op.drop_table("po_matches")
    op.drop_table("approval_tokens")
    op.drop_table("processing_logs")
    op.drop_table("extraction_confidence")
    op.drop_table("line_items")
    op.drop_table("extracted_data")
    op.drop_table("invoices")
    op.drop_table("purchase_orders")
    op.drop_table("vendors")
    op.drop_table("organizations")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS po_status")
    op.execute("DROP TYPE IF EXISTS payment_status")
    op.execute("DROP TYPE IF EXISTS po_match_status")
    op.execute("DROP TYPE IF EXISTS approval_status")
    op.execute("DROP TYPE IF EXISTS invoice_source")
    op.execute("DROP TYPE IF EXISTS invoice_status")
