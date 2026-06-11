"""transactions and milestones tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11

"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("listing_key", sa.String(36), nullable=False, index=True),
        sa.Column("stage", sa.String(32), nullable=False, index=True),
        sa.Column("parties", sa.JSON(), nullable=False),
        sa.Column("contract_date", sa.Date(), nullable=False),
        sa.Column("close_date", sa.Date()),
    )
    op.create_table(
        "milestones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(36), nullable=False, index=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("owner", sa.String(120), nullable=False, server_default=""),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_reason", sa.String(300)),
    )


def downgrade() -> None:
    op.drop_table("milestones")
    op.drop_table("transactions")
