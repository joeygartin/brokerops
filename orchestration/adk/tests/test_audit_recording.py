"""Audit-seam parity on ADK: the engine must publish run context so writes performed
on resume are recorded and linked to the approval — identically to the LangGraph side.
"""

from workflow_fixtures import GraphFakeCRM, GraphFakeMLS, make_engine

from brokerops_adk.workflows.listing_to_contract import build_listing_to_contract
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.models.mutation import MutationRecord
from brokerops_core.services.audit import RecordingCRM
from brokerops_core.services.workflow_runs import LISTING_TO_CONTRACT


class CollectingAuditLog:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


async def test_resume_writes_are_recorded_and_linked() -> None:
    audit = CollectingAuditLog()
    crm = RecordingCRM(GraphFakeCRM(), audit)
    engine, _ = make_engine(build_listing_to_contract(GraphFakeMLS(), crm))

    run = await engine.start(LISTING_TO_CONTRACT, {"listing_key": "RM1001"}, actor="joey@x")
    assert run.approval is not None
    # No external write before approval — the run is paused at the gate.
    assert audit.records == []

    decision = ApprovalDecision(decision=ApprovalStatus.APPROVED, decided_by="admin@x")
    await engine.decide(run.approval, decision)

    assert audit.records, "ADK engine did not publish audit context on resume"
    assert {r.tool for r in audit.records} == {"create_task"}
    for record in audit.records:
        assert record.workflow_run_id == run.thread_id
        assert record.workflow == LISTING_TO_CONTRACT
        assert record.approval_id == run.approval.id
        assert record.actor == "admin@x"
        assert record.outcome.value == "success"
        assert record.external_id
