"""add_webhook_delivery

Revision ID: 004
Revises: 003
Create Date: 2026-07-26 16:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("webhook_url", sa.String(1024), nullable=False),
        sa.Column(
            "event_type",
            sa.String(100),
            nullable=False,
            server_default=sa.text("'invoice.processed'"),
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
            index=True,
        ),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_webhook_deliveries_invoice_id"),
        "webhook_deliveries",
        ["invoice_id"],
    )
    op.create_index(
        op.f("ix_webhook_deliveries_organization_id"),
        "webhook_deliveries",
        ["organization_id"],
    )
    op.create_foreign_key(
        "fk_webhook_deliveries_invoice_id",
        "webhook_deliveries",
        "invoices",
        ["invoice_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_webhook_deliveries_organization_id",
        "webhook_deliveries",
        "organizations",
        ["organization_id"],
        ["id"],
    )

    # Add webhook_deliveries relationship to invoices
    # (relationship-only change, no DB column needed)


def downgrade() -> None:
    op.drop_constraint(
        "fk_webhook_deliveries_organization_id",
        "webhook_deliveries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_webhook_deliveries_invoice_id",
        "webhook_deliveries",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_webhook_deliveries_organization_id"),
        table_name="webhook_deliveries",
    )
    op.drop_index(
        op.f("ix_webhook_deliveries_invoice_id"),
        table_name="webhook_deliveries",
    )
    op.drop_table("webhook_deliveries")
