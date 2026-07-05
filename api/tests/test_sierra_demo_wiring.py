"""All three workflows, BOTH engines, over the Sierra stub in demo wiring (BOP-022).

The CRM adapter comes from `build_crm_adapter()` under CRM_VENDOR=sierra +
SIERRA_BASE_URL=internal — the exact zero-credential path a Sierra demo deploy
uses — and is wrapped in the same Idempotent(Recording(·)) write seam main.py
wires, so every CRM write these workflows perform is audited and deduped
against the second vendor exactly as against the first.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from brokerops_api.db import InMemoryApprovalRepo, InMemoryAuditLog, InMemoryIdempotencyStore
from brokerops_api.deps import build_crm_adapter
from brokerops_api.workflows import (
    LISTING_TO_CONTRACT,
    TRANSACTION_COORDINATION,
    VAPI_FOLLOWUP,
    WorkflowEngine,
)
from brokerops_adk.engine import build_engine as build_adk_engine
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.models.call import CallRecord
from brokerops_core.models.feedback import ShowingFeedback
from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery, ListingStatus
from brokerops_core.models.message import Message
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.audit import RecordingCRM, RecordingEmail
from brokerops_core.services.drafting import DeterministicDrafter
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_core.services.idempotency import IdempotentCRM, IdempotentEmail
from brokerops_core.services.message_send import MessageSendService
from brokerops_langgraph.engine import build_engine as build_langgraph_engine

TODAY = date.today()

LISTING = Listing(
    mls_id="RM1001",
    status=ListingStatus.ACTIVE,
    address="412 Alder Court, Rivermouth, CA 95890",
    city="Rivermouth",
    state="CA",
    postal_code="95890",
    list_price=489000,
    bedrooms=3,
    bathrooms=2,
    agent_id="AGT-001",
    agent_name="Dana Whitfield",
    modified_at=datetime(2026, 6, 8, tzinfo=UTC),
)

TXN = Transaction(
    id="TXN-9001",
    listing_key="RM1001",
    stage=TransactionStage.UNDER_CONTRACT,
    contract_date=TODAY - timedelta(days=10),
    close_date=TODAY + timedelta(days=20),
)

MILESTONE = Milestone(
    id="M-9001",
    transaction_id="TXN-9001",
    type=MilestoneType.INSPECTION,
    title="Inspection",
    due_date=TODAY + timedelta(days=2),
    owner="TC Team",
)

COOL_TRANSCRIPT = (
    "Nice house but it felt overpriced for the neighborhood. "
    "The bathroom needs updating. We will keep looking."
)

# A seeded Sierra stub lead (integrations/sierra_crm/stub.py).
SIERRA_CONTACT_ID = "501"


class FakeMLS:
    async def search_listings(self, query: ListingQuery) -> list[Listing]:
        return [LISTING]

    async def get_listing(self, listing_key: str) -> Listing | None:
        return LISTING if listing_key == "RM1001" else None

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]:
        return []


class FakeVoice:
    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, Any]
    ) -> str:
        return "call-x"

    async def get_call(self, call_id: str) -> CallRecord | None:
        return None


class FakeTransactionStore:
    def __init__(self) -> None:
        self.milestones = {MILESTONE.id: MILESTONE}

    async def create_transaction(
        self, transaction: Transaction, milestones: list[Milestone]
    ) -> None:
        raise NotImplementedError

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        return TXN if transaction_id == TXN.id else None

    async def get_milestone(self, milestone_id: str) -> Milestone | None:
        return self.milestones.get(milestone_id)

    async def list_active_transactions(self) -> list[Transaction]:
        return [TXN]

    async def list_milestones(self, transaction_id: str) -> list[Milestone]:
        return list(self.milestones.values())

    async def set_escalation_level(self, milestone_id: str, level: int) -> None:
        existing = self.milestones[milestone_id]
        self.milestones[milestone_id] = existing.model_copy(update={"escalation_level": level})


class FakeEmail:
    async def send(self, message: Message) -> str:
        return "provider-1"


class DictMessageStore:
    def __init__(self) -> None:
        self.rows: dict[str, Message] = {}

    async def save_message(self, message: Message) -> None:
        self.rows[message.id] = message

    async def get_message(self, message_id: str) -> Message | None:
        return self.rows.get(message_id)

    async def list_messages(self, contact_id: str | None = None, limit: int = 100) -> list[Message]:
        return list(self.rows.values())[:limit]


class FakeFeedbackStore:
    def __init__(self) -> None:
        self.call_records: dict[str, CallRecord] = {}
        self.feedback: dict[str, ShowingFeedback] = {}

    async def save_call_record(self, record: CallRecord) -> None:
        self.call_records[record.vapi_call_id] = record

    async def get_call_record(self, vapi_call_id: str) -> CallRecord | None:
        return self.call_records.get(vapi_call_id)

    async def upsert_feedback(self, feedback: ShowingFeedback) -> str:
        self.feedback[feedback.id] = feedback
        return feedback.id

    async def get_feedback(self, feedback_id: str) -> ShowingFeedback | None:
        return self.feedback.get(feedback_id)

    async def list_feedback(self, listing_key: str) -> list[ShowingFeedback]:
        return [f for f in self.feedback.values() if f.listing_key == listing_key]


EngineFactory = Callable[..., AbstractAsyncContextManager[WorkflowEngine]]
ENGINE_FACTORIES: dict[str, EngineFactory] = {
    "langgraph": build_langgraph_engine,
    "adk": build_adk_engine,
}


@pytest.fixture(autouse=True)
def sierra_internal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_VENDOR", "sierra")
    monkeypatch.setenv("SIERRA_BASE_URL", "internal")
    monkeypatch.delenv("SIERRA_API_KEY", raising=False)


Harness = tuple[WorkflowEngine, InMemoryApprovalRepo, InMemoryAuditLog]


@pytest.fixture(params=["langgraph", "adk"])
async def harness(request: pytest.FixtureRequest) -> AsyncIterator[Harness]:
    audit_log = InMemoryAuditLog()
    repo = InMemoryApprovalRepo()
    idempotency = InMemoryIdempotencyStore()
    engine_crm = IdempotentCRM(
        RecordingCRM(build_crm_adapter(), audit_log, integration="sierra"),
        idempotency,
    )
    # Same seam main.py wires for outbound email (BOP-019): approved drafted
    # sends are deduped and audited exactly like the CRM writes.
    message_service = MessageSendService(
        email=IdempotentEmail(RecordingEmail(FakeEmail(), audit_log), idempotency),
        store=DictMessageStore(),
        drafting=DeterministicDrafter(),
    )
    async with ENGINE_FACTORIES[request.param](
        mls=FakeMLS(),
        crm=engine_crm,
        voice=FakeVoice(),
        extraction=DeterministicExtractor(),
        transaction_store=FakeTransactionStore(),
        feedback_store=FakeFeedbackStore(),
        approval_repo=repo,
        message_service=message_service,
        database_url=None,
    ) as engine:
        yield engine, repo, audit_log


async def _approve(engine: WorkflowEngine, repo: InMemoryApprovalRepo, approval_id: str) -> Any:
    approval = await repo.get(approval_id)
    assert approval is not None
    return await engine.decide(
        approval, ApprovalDecision(decision=ApprovalStatus.APPROVED, decided_by="demo-operator")
    )


async def test_listing_to_contract_publishes_tasks_to_sierra(harness: Harness) -> None:
    engine, repo, audit = harness
    started = await engine.start(LISTING_TO_CONTRACT, {"listing_key": "RM1001"})
    assert started.status == "awaiting_approval"
    assert started.approval is not None

    result = await _approve(engine, repo, started.approval.id)
    assert result.status == "completed"
    assert result.output is not None
    assert len(result.output["fub_task_ids"]) >= 3
    # every task write went through the seam and was attributed to sierra
    task_writes = [m for m in await audit.list() if m.tool == "create_task"]
    assert len(task_writes) == len(result.output["fub_task_ids"])
    assert {m.integration for m in task_writes} == {"sierra"}


async def test_transaction_coordination_sends_reminders_via_sierra(harness: Harness) -> None:
    engine, _repo, audit = harness
    result = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": "TXN-9001"})
    assert result.status == "completed"
    assert result.output is not None
    assert result.output["outcome"] == "reminders_sent"
    assert len(result.output["reminder_task_ids"]) == 1
    task_writes = [m for m in await audit.list() if m.tool == "create_task"]
    assert [m.integration for m in task_writes] == ["sierra"]


async def test_vapi_followup_syncs_note_and_call_log_to_sierra(harness: Harness) -> None:
    engine, repo, audit = harness
    result = await engine.start(
        VAPI_FOLLOWUP,
        {
            "call_id": "call-cool-1",
            "listing_key": "RM1001",
            "contact_id": SIERRA_CONTACT_ID,
            "transcript": COOL_TRANSCRIPT,
            "call_outcome": "customer-ended-call",
        },
    )
    # The Sierra stub lead has an email on file, so the synced path drafts a
    # follow-up and pauses at the outbound-message gate (BOP-019).
    assert result.status == "awaiting_approval"
    assert result.approval is not None
    assert result.approval.kind == "approve_outbound_message"
    assert result.approval.payload["recipient"] == "morgan.ellis@example.test"

    decided = await _approve(engine, repo, result.approval.id)
    assert decided.status == "completed"
    assert decided.output is not None
    assert decided.output["outcome"] == "followup_sent"
    assert decided.output["note_id"]
    assert decided.output["call_log_id"]
    mutations = await audit.list()
    assert {m.tool for m in mutations} == {"add_note", "log_call", "send_email"}
    # CRM writes attribute to sierra; the email send crossed its own seam
    assert {m.integration for m in mutations if m.tool != "send_email"} == {"sierra"}
    (email_write,) = [m for m in mutations if m.tool == "send_email"]
    assert email_write.integration == "email" and email_write.approval_id == result.approval.id
