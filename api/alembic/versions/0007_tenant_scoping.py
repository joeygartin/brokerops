"""tenant_id columns + row-level security (BOP-006 tenant scoping)

Adds a tenant_id to every agent-reachable table and a forced row-level-security
policy keyed on the per-transaction tenant GUC the application binds
(``api/db.py`` ``TenantScopedEngine``). This is the database belt under the
app-layer scoped stores: a query that skips the app scope is still confined here.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-28

"""

import os

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# The tenant GUC the app binds per transaction; the RLS policy and the column
# default both read it. ``missing_ok => true`` makes an unset GUC return NULL, so an
# unscoped statement matches no row (read) and violates NOT NULL (write) — fail closed.
_GUC = "current_setting('app.brokerops_tenant', true)"

# Agent-reachable tables confined to one tenant. idempotency_keys + magic_login_tokens
# are intentionally excluded in increment 1 (internal infra / pre-auth, no tenant
# context bound at token request time); the orchestration engines' own checkpoint /
# session tables are managed by those engines, not here.
_TABLES = (
    "approval_requests",
    "transactions",
    "milestones",
    "call_records",
    "showing_feedback",
    "mutation_records",
)


def upgrade() -> None:
    # Existing rows predate scoping; they belong to this single-tenant deploy, so
    # backfill them to the deploy's tenant (read from the env the migration runs in)
    # before NOT NULL + RLS, or the policy would hide them.
    backfill_tenant = os.environ.get("TENANT_ID", "demo")
    for table in _TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.String(64), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET tenant_id = :t").bindparams(t=backfill_tenant))
        op.alter_column(table, "tenant_id", nullable=False, server_default=sa.text(_GUC))
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        # FORCE so the policy also applies to the table owner (the app's DB user); the
        # backfill above already ran, so no existing row is stranded.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {_GUC}) WITH CHECK (tenant_id = {_GUC})"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")
