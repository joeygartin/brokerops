"""documents table + milestone expected_document (BOP-021)

`documents` holds metadata/pointers only — a `FileRef` into the office file
store plus the transaction/milestone it belongs to. No file bytes ever land in
the database. Tenant-scoped exactly like every other agent-reachable table:
tenant_id defaulted from the per-transaction GUC and confined by a forced
row-level-security policy (the migration-0007 pattern).

`milestones.expected_document` lets a timeline step declare the document kind
that proves it (purchase agreement, disclosures, …) — a read-only reporting
signal, no new workflow branches.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-04

"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# Same GUC the 0007 policies read; an unset GUC returns NULL, so an unscoped
# statement matches no row (read) and violates NOT NULL (write) — fail closed.
_GUC = "current_setting('app.brokerops_tenant', true)"


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default=sa.text(_GUC)),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("milestone_id", sa.String(64)),
        sa.Column("kind", sa.String(32), nullable=False, server_default="other"),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        # The storage-neutral FileRef pointer (file_id, name, mime_type,
        # size_bytes, web_url) — metadata only, never content.
        sa.Column("file", sa.JSON(), nullable=False),
        sa.Column("uploaded_by", sa.String(254), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_transaction_id", "documents", ["transaction_id"])
    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON documents "
        f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})"
    )

    op.add_column("milestones", sa.Column("expected_document", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("milestones", "expected_document")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON documents")
    op.drop_index("ix_documents_transaction_id", table_name="documents")
    op.drop_index("ix_documents_tenant_id", table_name="documents")
    op.drop_table("documents")
