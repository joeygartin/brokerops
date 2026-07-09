"""mutation_records.transaction_id — per-deal audit slice (BOP-027)

Adds the deal identifier the transaction hub's audit slice matches on. Every write
performed inside a transaction-scoped workflow run is stamped with it at record
time (via the run's AuditContext), so ``/audit?transaction_id=`` is a complete,
direct column match — independent of whether the run ever raised an approval, and
never bounded by a scan window. ``server_default=""`` backfills existing rows as
un-scoped (a listing workflow, a security denial), matching the model default.

Rows recorded BEFORE this deploy predate the record-time stamping, so the upgrade
also backfills their transaction_id from the evidence already on hand (review r6):
an approval's graph_thread_id (== the run's workflow_run_id) carries the deal id in
its payload, and a direct send's recorded args carry it too. Best-effort — a
historical run that wrote CRM tasks but never raised an approval and never carried
the id in args cannot be recovered (no evidence links it), but going forward every
such write is stamped at record time.

Table-level grants from migration 0010 cover the new column, so the least-privilege
runtime role needs no re-grant. Idempotent-friendly for the local/CI owner path.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-09

"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


# Lightweight refs (self-contained — no app-model import): only the columns the
# backfill reads/writes, typed so the JSON accessors compile per dialect
# (Postgres ->> / SQLite json_extract).
_mut = sa.table(
    "mutation_records",
    sa.column("workflow_run_id", sa.String()),
    sa.column("transaction_id", sa.String()),
    sa.column("args", sa.JSON()),
)
_appr = sa.table(
    "approval_requests",
    sa.column("graph_thread_id", sa.String()),
    sa.column("payload", sa.JSON()),
)


def backfill_transaction_ids(bind: sa.engine.Connection) -> None:
    """Stamp transaction_id onto pre-existing mutation_records from stored evidence.

    Two passes, both touching only still-unscoped rows: first from the transaction's
    approvals (the run's writes share the approval's graph_thread_id == workflow_run_id),
    then from a direct send's args-tagged transaction_id. Kept separate so a send that
    ran under an approved gate is scoped by the run match; a gateless direct send falls
    through to the args tag.
    """
    appr_tid = _appr.c.payload["transaction_id"].as_string()
    run_match = (
        sa.select(appr_tid)
        .where(_appr.c.graph_thread_id == _mut.c.workflow_run_id)
        .where(appr_tid.isnot(None))
        .where(appr_tid != "")
        .limit(1)
        .scalar_subquery()
    )
    # COALESCE keeps rows with no matching approval unchanged ("").
    bind.execute(
        _mut.update()
        .where(_mut.c.transaction_id == "")
        .values(transaction_id=sa.func.coalesce(run_match, ""))
    )

    args_tid = _mut.c.args["transaction_id"].as_string()
    bind.execute(
        _mut.update()
        .where(_mut.c.transaction_id == "")
        .where(args_tid.isnot(None))
        .where(args_tid != "")
        .values(transaction_id=args_tid)
    )


def upgrade() -> None:
    op.add_column(
        "mutation_records",
        sa.Column("transaction_id", sa.String(36), nullable=False, server_default=""),
    )
    op.create_index("ix_mutation_records_transaction_id", "mutation_records", ["transaction_id"])
    backfill_transaction_ids(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_mutation_records_transaction_id", table_name="mutation_records")
    op.drop_column("mutation_records", "transaction_id")
