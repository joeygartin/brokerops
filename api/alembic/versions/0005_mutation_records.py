"""mutation records (action audit-ledger) table

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-22

"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mutation_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(64), nullable=False, server_default="", index=True),
        sa.Column("workflow", sa.String(64), nullable=False, server_default=""),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("integration", sa.String(32), nullable=False, index=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("approval_id", sa.String(36)),
        sa.Column("actor", sa.String(120)),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(120)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mutation_records")
