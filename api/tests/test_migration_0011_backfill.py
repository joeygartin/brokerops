"""Migration 0011 backfill — pre-deploy audit rows land in the transaction slice.

Records written before record-time transaction_id stamping (transaction_id="") must
be recovered from stored evidence on upgrade (BOP-027 review r6): an approval's
graph_thread_id (== the run's workflow_run_id) carries the deal id, and a direct
send's args carry it. Exercised on SQLite via the migration's own backfill function
so the JSON accessors are proven to compile off Postgres too.
"""

import importlib.util
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

from brokerops_api.db import approval_requests, metadata, mutation_records

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0011_mutation_records_transaction_id.py"
)


def _load_backfill() -> Callable[[sa.engine.Connection], None]:
    spec = importlib.util.spec_from_file_location("m0011", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    loaded: Callable[[sa.engine.Connection], None] = module.backfill_transaction_ids
    return loaded


def _mutation(*, run: str, args: dict[str, object]) -> dict[str, object]:
    return {
        "id": uuid4().hex,
        "tenant_id": "demo",
        "workflow_run_id": run,
        "workflow": "transaction_coordination",
        "transaction_id": "",  # pre-deploy: unstamped
        "tool": "create_task",
        "integration": "followupboss",
        "args": args,
        "outcome": "success",
        "created_at": datetime.now(UTC),
    }


def test_backfill_recovers_transaction_id_from_approvals_and_args() -> None:
    backfill = _load_backfill()
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn:
        # An escalation approval ties run "run-appr" to TXN-1001.
        conn.execute(
            approval_requests.insert().values(
                id="ap-1",
                tenant_id="demo",
                workflow="transaction_coordination",
                graph_thread_id="run-appr",
                kind="approve_escalation",
                payload={"transaction_id": "TXN-1001"},
                status="approved",
                created_at=datetime.now(UTC),
            )
        )
        conn.execute(
            mutation_records.insert(),
            [
                # Recovered via the approval's run match (create_task, no id in args).
                _mutation(run="run-appr", args={"name": "URGENT: overdue"}),
                # Recovered via the args tag (a direct send, no approval for its run).
                _mutation(run="run-send", args={"transaction_id": "TXN-1002", "recipient": "x@y"}),
                # No evidence at all → stays unscoped ("").
                _mutation(run="run-orphan", args={"name": "housekeeping"}),
            ],
        )

        backfill(conn)

        rows = conn.execute(
            sa.select(mutation_records.c.workflow_run_id, mutation_records.c.transaction_id)
        ).all()
    by_run = {r.workflow_run_id: r.transaction_id for r in rows}
    assert by_run["run-appr"] == "TXN-1001"
    assert by_run["run-send"] == "TXN-1002"
    assert by_run["run-orphan"] == ""
