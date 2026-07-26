"""add_approval_po_vendor_payment

Revision ID: 003
Revises: 002
Create Date: 2026-07-26 15:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Create ENUM types (PostgreSQL) ──────────────────────────────
    # approval_status: pending_approval / approved / rejected / auto_approved
    sa.Enum(
        "pending_approval", "approved", "rejected", "auto_approved",
        name="approval_status",
    ).create(op.get_bind(), checkfirst=True)

    # po_match_status: matched / partial / unmatched / discrepancy
    sa.Enum(
        "matched", "partial", "unmatched", "discrepancy",
        name="po_match_status",
    ).create(op.get_bind(), checkfirst=True)

    # payment_status: unpaid / paid / overdue
    sa.Enum(
        "unpaid", "paid", "overdue",
        name="payment_status",
    ).create(op.get_bind(), checkfirst=True)

    # po_status: open / fulfilled / cancelled
    sa.Enum(
        "open", "fulfilled", "cancelled",
        name="po_status",
    ).create(op.get_bind(), checkfirst=True)

    # ── Create approval_tokens table ────────────────────────────────
    op.create_table(
        "approval_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(36), nullable=False),
        sa.Column("approver_email", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_approval_tokens_invoice_id"),
        "approval_tokens", ["invoice_id"],
    )
    op.create_index(
        op.f("ix_approval_tokens_token"),
        "approval_tokens", ["token"],
        unique=True,
    )
    op.create_foreign_key(
        "fk_approval_tokens_invoice_id",
        "approval_tokens", "invoices",
        ["invoice_id"], ["id"],
    )

    # ── Create purchase_orders table ────────────────────────────────
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("po_number", sa.String(255), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("line_items", sa.Text(), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("status",
                  sa.Enum("open", "fulfilled", "cancelled",
                          name="po_status", create_type=False),
                  nullable=False, server_default=sa.text("'open'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("po_number"),
    )
    op.create_index(
        op.f("ix_purchase_orders_po_number"),
        "purchase_orders", ["po_number"],
    )

    # ── Create po_matches table ─────────────────────────────────────
    op.create_table(
        "po_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("po_id", sa.Uuid(), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("discrepancies", sa.Text(), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_po_matches_invoice_id"),
        "po_matches", ["invoice_id"],
    )
    op.create_index(
        op.f("ix_po_matches_po_id"),
        "po_matches", ["po_id"],
    )
    op.create_foreign_key(
        "fk_po_matches_invoice_id",
        "po_matches", "invoices",
        ["invoice_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_po_matches_po_id",
        "po_matches", "purchase_orders",
        ["po_id"], ["id"],
    )

    # ── Create vendors table ────────────────────────────────────────
    op.create_table(
        "vendors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("aliases", sa.Text(), nullable=True),
        sa.Column("tax_id", sa.String(100), nullable=True),
        sa.Column("payment_terms", sa.String(255), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_name"),
    )
    op.create_index(
        op.f("ix_vendors_canonical_name"),
        "vendors", ["canonical_name"],
    )

    # ── Add columns to invoices ─────────────────────────────────────
    # Approval workflow
    op.add_column(
        "invoices",
        sa.Column("approval_status",
                  sa.Enum("pending_approval", "approved", "rejected", "auto_approved",
                          name="approval_status", create_type=False),
                  nullable=False, server_default=sa.text("'pending_approval'")),
    )
    op.create_index(
        op.f("ix_invoices_approval_status"),
        "invoices", ["approval_status"],
    )

    # PO matching
    op.add_column(
        "invoices",
        sa.Column("matched_po_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("po_match_status",
                  sa.Enum("matched", "partial", "unmatched", "discrepancy",
                          name="po_match_status", create_type=False),
                  nullable=True),
    )
    op.create_index(
        op.f("ix_invoices_matched_po_id"),
        "invoices", ["matched_po_id"],
    )
    op.create_foreign_key(
        "fk_invoices_matched_po_id",
        "invoices", "purchase_orders",
        ["matched_po_id"], ["id"],
    )

    # Vendor matching
    op.add_column(
        "invoices",
        sa.Column("vendor_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("match_confidence", sa.Float(), nullable=True),
    )
    op.create_index(
        op.f("ix_invoices_vendor_id"),
        "invoices", ["vendor_id"],
    )
    op.create_foreign_key(
        "fk_invoices_vendor_id",
        "invoices", "vendors",
        ["vendor_id"], ["id"],
    )

    # Payment tracking
    op.add_column(
        "invoices",
        sa.Column("due_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("payment_status",
                  sa.Enum("unpaid", "paid", "overdue",
                          name="payment_status", create_type=False),
                  nullable=True, server_default=sa.text("'unpaid'")),
    )
    op.create_index(
        op.f("ix_invoices_due_date"),
        "invoices", ["due_date"],
    )
    op.create_index(
        op.f("ix_invoices_payment_status"),
        "invoices", ["payment_status"],
    )

    # ── Add columns to extracted_data ───────────────────────────────
    op.add_column(
        "extracted_data",
        sa.Column("vendor_verified", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "extracted_data",
        sa.Column("vendor_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_extracted_data_vendor_id"),
        "extracted_data", ["vendor_id"],
    )
    op.create_foreign_key(
        "fk_extracted_data_vendor_id",
        "extracted_data", "vendors",
        ["vendor_id"], ["id"],
    )


def downgrade() -> None:
    # ── Drop columns from extracted_data ────────────────────────────
    op.drop_constraint(
        "fk_extracted_data_vendor_id",
        "extracted_data", type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_extracted_data_vendor_id"),
        table_name="extracted_data",
    )
    op.drop_column("extracted_data", "vendor_id")
    op.drop_column("extracted_data", "vendor_verified")

    # ── Drop columns from invoices ──────────────────────────────────
    # Payment tracking
    op.drop_index(op.f("ix_invoices_payment_status"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_due_date"), table_name="invoices")
    op.drop_column("invoices", "payment_status")
    op.drop_column("invoices", "due_date")

    # Vendor matching
    op.drop_constraint(
        "fk_invoices_vendor_id",
        "invoices", type_="foreignkey",
    )
    op.drop_index(op.f("ix_invoices_vendor_id"), table_name="invoices")
    op.drop_column("invoices", "match_confidence")
    op.drop_column("invoices", "vendor_id")

    # PO matching
    op.drop_constraint(
        "fk_invoices_matched_po_id",
        "invoices", type_="foreignkey",
    )
    op.drop_index(op.f("ix_invoices_matched_po_id"), table_name="invoices")
    op.drop_column("invoices", "po_match_status")
    op.drop_column("invoices", "matched_po_id")

    # Approval workflow
    op.drop_index(op.f("ix_invoices_approval_status"), table_name="invoices")
    op.drop_column("invoices", "approval_status")

    # ── Drop tables ─────────────────────────────────────────────────
    op.drop_table("vendors")
    op.drop_table("po_matches")
    op.drop_table("purchase_orders")
    op.drop_table("approval_tokens")

    # ── Drop ENUM types (PostgreSQL) ────────────────────────────────
    sa.Enum(name="approval_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="po_match_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="payment_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="po_status").drop(op.get_bind(), checkfirst=True)
