"""cron_runs — last per-job scheduled-trigger outcome (BOP-035)

Instance-level ops signal (not tenant agent data): the milestone cron (and any
future scheduled job) writes one row per job name so /statusz and deploy-side
alerting can answer "have milestones run in the last 24h?". Single-row upsert
keyed by job — only the latest outcome is retained. No RLS: this is fleet
infrastructure shared with idempotency_keys / magic_login_tokens, reached over
the raw engine (not TenantScopedEngine).

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-31

"""

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_APP_ROLE = os.environ.get("APP_DB_ROLE", "brokerops_app")


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
    op.create_table(
        "cron_runs",
        sa.Column("job", sa.String(64), primary_key=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_pending_escalation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_tail_suppressed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
    )

    # Same least-privilege grant pattern as 0010: only the verbs the store uses.
    if _role_exists():
        op.execute(f'GRANT SELECT, INSERT, UPDATE ON "cron_runs" TO {_role()}')


def downgrade() -> None:
    if _role_exists():
        op.execute(f'REVOKE ALL PRIVILEGES ON "cron_runs" FROM {_role()}')
    op.drop_table("cron_runs")
