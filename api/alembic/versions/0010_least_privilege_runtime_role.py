"""least-privilege runtime DB role grants (BOP-013)

Grants a non-owner application role (``brokerops_app``) DML-only access to the
app tables so the runtime connects with a role that is neither the table owner
nor a ``BYPASSRLS`` role. The forced row-level-security policy (migration 0007)
therefore binds to every runtime query, and the app can no longer run DDL on —
or disable RLS on — the tables it reads. Schema management (this migration plus
the LangGraph checkpointer's ``setup()``) keeps the owner role, selected by
``MIGRATION_DATABASE_URL``; the tenant-scoped domain stores use ``DATABASE_URL``,
which in a hardened deploy is this runtime role (ADR-0021).

The runtime role is provisioned out-of-band — Terraform ``google_sql_user`` on
Cloud SQL. This migration only grants privileges to it and is a **no-op where the
role is absent** (local compose / CI run as the single owner role), so the
zero-credential path is unchanged. Idempotent and safely re-runnable.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-07

"""

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# The runtime role name Terraform provisions; overridable so a deploy can rename
# it. Validated as a bare SQL identifier before interpolation (GRANT has no bind
# parameters for role names) — never trusts the value blindly.
_APP_ROLE = os.environ.get("APP_DB_ROLE", "brokerops_app")

# Per-table DML grants — an explicit allowlist, NOT "ALL TABLES", and each table
# gets ONLY the verbs its runtime SQL store actually executes
# (api/src/brokerops_api/db.py), so the grant is least-privilege at the operation
# level, not merely the table level. Never CREATE/ALTER/DROP/TRUNCATE, and never a
# blanket default-privilege grant — so alembic_version (migration bookkeeping) and
# the LangGraph checkpointer's own tables (owned and reached only by the owner DSN)
# stay entirely out of the runtime's reach.
#
# Notably the audit ledger `mutation_records` is INSERT+SELECT only: a compromised
# runtime DSN can append and read its tenant's action history but can never rewrite
# or delete it, keeping the ledger durable and complete (ADR-0010). `documents` is
# likewise append-only. SELECT rides along wherever the store runs an UPDATE/DELETE
# (their WHERE/RETURNING clauses read columns, which Postgres gates on SELECT).
#
# A future runtime table adds its own entry here (or a GRANT in the migration that
# creates it) — the same one-line pattern — rather than reviving a blanket grant.
_RUNTIME_GRANTS = {
    "approval_requests": "SELECT, INSERT, UPDATE",  # create / list / mark_decided
    "transactions": "SELECT, INSERT, DELETE",  # create / list / demo clear()
    "milestones": "SELECT, INSERT, UPDATE, DELETE",  # + set_escalation_level / clear()
    "call_records": "SELECT, INSERT, UPDATE",  # upsert
    "showing_feedback": "SELECT, INSERT, UPDATE",  # upsert
    "mutation_records": "SELECT, INSERT",  # append-only audit ledger (ADR-0010)
    "outbound_messages": "SELECT, INSERT, UPDATE",  # save / advance_status
    "documents": "SELECT, INSERT",  # add / list — never mutated
    "idempotency_keys": "SELECT, INSERT, UPDATE",  # begin / complete
    "magic_login_tokens": "SELECT, INSERT, UPDATE",  # create / consume (UPDATE…RETURNING)
}


def _role() -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", _APP_ROLE):
        raise ValueError(f"invalid APP_DB_ROLE {_APP_ROLE!r}: must be a bare SQL identifier")
    return f'"{_APP_ROLE}"'


def _role_exists() -> bool:
    return (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _APP_ROLE})
        .first()
        is not None
    )


def upgrade() -> None:
    role = _role()
    if not _role_exists():
        # Local compose / CI / Postgres-gated tests run as the single owner role;
        # there is no separate runtime role to grant to. No-op keeps those paths
        # (and the zero-credential demo) byte-for-byte unchanged.
        return

    # Read the schema, but never author it: the runtime role owns nothing and
    # cannot create tables, so it cannot escape RLS by making an unpoliced table.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {role}")

    # Per-table DML from the allowlist — never ALL TABLES or default privileges, so
    # alembic_version and the checkpointer's tables stay untouchable. DDL stays
    # owner-only, so the runtime cannot DISABLE/NO FORCE the RLS policy either.
    for table, privileges in _RUNTIME_GRANTS.items():
        op.execute(f'GRANT {privileges} ON "{table}" TO {role}')

    # Best-effort: shed the Cloud SQL cloudsqlsuperuser membership so the role
    # cannot SET ROLE to a broader one. This needs ADMIN on that role; if the
    # migration owner lacks it the REVOKE errors — swallow it via a savepoint and
    # leave membership as-is. The security property does not depend on this:
    # non-owner + no BYPASSRLS already makes the RLS policy bind, and membership in
    # cloudsqlsuperuser confers neither table ownership nor the superuser attribute.
    op.execute("SAVEPOINT bop013_shed_super")
    try:
        op.execute(f"REVOKE cloudsqlsuperuser FROM {role}")
        op.execute("RELEASE SAVEPOINT bop013_shed_super")
    except Exception:  # noqa: BLE001 — any failure means "not permitted"; keep going
        op.execute("ROLLBACK TO SAVEPOINT bop013_shed_super")


def downgrade() -> None:
    role = _role()
    if not _role_exists():
        return
    for table in _RUNTIME_GRANTS:
        op.execute(f'REVOKE ALL PRIVILEGES ON "{table}" FROM {role}')
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role}")
