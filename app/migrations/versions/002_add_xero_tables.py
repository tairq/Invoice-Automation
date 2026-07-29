"""add_xero_tables

Revision ID: 002
Revises: 001
Create Date: 2026-07-26 14:01:00.000000
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
    # Create xero_credentials table
    op.create_table(
        "xero_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )
    op.create_index(
        op.f("ix_xero_credentials_organization_id"),
        "xero_credentials",
        ["organization_id"],
    )
    op.create_foreign_key(
        "fk_xero_credentials_organization_id",
        "xero_credentials",
        "organizations",
        ["organization_id"],
        ["id"],
    )

    # Add xero_invoice_id to invoices
    op.add_column(
        "invoices",
        sa.Column("xero_invoice_id", sa.String(255), nullable=True),
    )
    op.create_index(
        op.f("ix_invoices_xero_invoice_id"),
        "invoices",
        ["xero_invoice_id"],
    )


def downgrade() -> None:
    # Drop column and index
    op.drop_index(op.f("ix_invoices_xero_invoice_id"), table_name="invoices")
    op.drop_column("invoices", "xero_invoice_id")

    # Drop table
    op.drop_constraint(
        "fk_xero_credentials_organization_id",
        "xero_credentials",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_xero_credentials_organization_id"),
        table_name="xero_credentials",
    )
    op.drop_table("xero_credentials")
