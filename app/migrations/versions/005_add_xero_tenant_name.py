"""add_xero_tenant_name

Revision ID: 005
Revises: 004
Create Date: 2026-07-28 16:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "xero_credentials",
        sa.Column("tenant_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("xero_credentials", "tenant_name")
