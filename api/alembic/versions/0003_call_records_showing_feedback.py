"""call records and showing feedback tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-11

"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_records",
        sa.Column("vapi_call_id", sa.String(64), primary_key=True),
        sa.Column("contact_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("listing_key", sa.String(36), nullable=False, server_default="", index=True),
        sa.Column("transcript", sa.Text(), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(64), nullable=False, server_default=""),
        sa.Column("extracted", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "showing_feedback",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("listing_key", sa.String(36), nullable=False, index=True),
        sa.Column("contact_id", sa.String(36), nullable=False),
        sa.Column("call_id", sa.String(64)),
        sa.Column("source", sa.String(16), nullable=False, server_default="call"),
        sa.Column("sentiment", sa.String(16), nullable=False, server_default="neutral"),
        sa.Column("structured_answers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("showing_feedback")
    op.drop_table("call_records")
