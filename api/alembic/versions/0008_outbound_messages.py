"""outbound messages table (BOP-015 comms history, one table for all channels)

Domain data — the outbound communication history (the `call_records` precedent),
not audit-ledger data: the ledger records the mutation crossing the provider
boundary; this records the communication itself. Channel is a column, so SMS
(BOP-018) reuses the table wholesale.

Tenant-scoped from birth: the table carries the same GUC-derived tenant_id default
and forced row-level-security policy migration 0007 applied to the existing
agent-reachable tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-04

"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# The tenant GUC the app binds per transaction (see migration 0007 / db.py
# TenantScopedEngine). ``missing_ok => true`` makes an unset GUC return NULL, so an
# unscoped statement matches no row (read) and violates NOT NULL (write) — fail closed.
_GUC = "current_setting('app.brokerops_tenant', true)"


def upgrade() -> None:
    op.create_table(
        "outbound_messages",
        # SHA-256 hex within a run (deterministic replay identity), uuid4 hex otherwise.
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id", sa.String(64), nullable=False, server_default=sa.text(_GUC), index=True
        ),
        sa.Column("channel", sa.String(16), nullable=False, server_default="email"),
        sa.Column("recipient", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("template_ref", sa.String(120), nullable=False, server_default=""),
        sa.Column("contact_id", sa.String(36), nullable=False, server_default="", index=True),
        sa.Column("listing_key", sa.String(36), nullable=False, server_default="", index=True),
        sa.Column("transaction_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="drafted"),
        sa.Column("provider_message_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.execute("ALTER TABLE outbound_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbound_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON outbound_messages "
        f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})"
    )


def downgrade() -> None:
    op.drop_table("outbound_messages")
