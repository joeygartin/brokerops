"""Fakes + a thin engine harness for the ADK workflow tests.

The port fakes are identical to the LangGraph suite's. The tests drive the
real AdkWorkflowEngine (not a bespoke runner loop) so the interrupt →
ApprovalRequest → resume path under test is the one the API uses.
"""

from datetime import UTC, date, datetime
from itertools import count
from typing import Any

from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.workflow import Workflow

from brokerops_adk.engine import USER_ID, AdkWorkflowEngine
from brokerops_adk.sessions import build_session_service
from brokerops_core.models.approval import ApprovalRequest, ApprovalStatus
from brokerops_core.models.call import CallRecord
from brokerops_core.models.contact import Contact, ContactCreate, CrmTask
from brokerops_core.models.feedback import ShowingFeedback
from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery, ListingStatus
from brokerops_core.models.milestone import Milestone
from brokerops_core.models.transaction import Transaction

LISTINGS = {
    "RM1001": Listing(
        mls_id="RM1001",
        status=ListingStatus.ACTIVE,
        address="412 Alder Court, Rivermouth, CA 95890",
        city="Rivermouth",
        state="CA",
        postal_code="95890",
        list_price=489000,
        bedrooms=3,
        bathrooms=2,
        living_area_sqft=1750,
        year_built=1999,
        agent_id="AGT-001",
        agent_name="Dana Whitfield",
        remarks="Updated single-story.",
        modified_at=datetime(2026, 6, 8, tzinfo=UTC),
    ),
    "RM1007": Listing(
        mls_id="RM1007",
        status=ListingStatus.CLOSED,
        address="1310 Orchard Lane, Rivermouth, CA 95890",
        city="Rivermouth",
        state="CA",
        postal_code="95890",
        list_price=432500,
        bedrooms=3,
        bathrooms=2,
        agent_id="AGT-001",
        agent_name="Dana Whitfield",
        modified_at=datetime(2026, 5, 28, tzinfo=UTC),
    ),
}


class GraphFakeMLS:
    async def search_listings(self, query: ListingQuery) -> list[Listing]:
        return list(LISTINGS.values())[: query.limit]

    async def get_listing(self, listing_key: str) -> Listing | None:
        return LISTINGS.get(listing_key)

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]:
        return []


class GraphFakeCRM:
    def __init__(self) -> None:
        self.created_tasks: list[CrmTask] = []
        self.notes: list[tuple[str, str, str]] = []
        self.logged_calls: list[tuple[str, str, str]] = []
        self._ids = count(9000)

    async def get_contact(self, contact_id: str) -> Contact | None:
        return None

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]:
        return []

    async def create_contact(self, draft: ContactCreate) -> Contact:
        return Contact(fub_id=str(next(self._ids)), name=f"{draft.first_name} {draft.last_name}")

    async def add_note(self, contact_id: str, subject: str, body: str) -> str:
        self.notes.append((contact_id, subject, body))
        return str(next(self._ids))

    async def create_task(
        self, name: str, due_date: date | None = None, contact_id: str | None = None
    ) -> CrmTask:
        task = CrmTask(id=str(next(self._ids)), name=name, due_date=due_date)
        self.created_tasks.append(task)
        return task

    async def log_call(
        self, contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0
    ) -> str:
        self.logged_calls.append((contact_id, outcome, note))
        return str(next(self._ids))


class FakeVoice:
    def __init__(self, calls: dict[str, CallRecord] | None = None) -> None:
        self.calls = calls or {}
        self.placed: list[tuple[str, str, dict[str, object]]] = []
        self._ids = count(7000)

    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, object]
    ) -> str:
        call_id = f"call-{next(self._ids)}"
        self.placed.append((contact_id, assistant_id, context))
        return call_id

    async def get_call(self, call_id: str) -> CallRecord | None:
        return self.calls.get(call_id)


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

    async def list_feedback(self, listing_key: str) -> list[ShowingFeedback]:
        return [f for f in self.feedback.values() if f.listing_key == listing_key]


class FakeTransactionStore:
    def __init__(self, transactions: list[Transaction], milestones: list[Milestone]) -> None:
        self._transactions = {t.id: t for t in transactions}
        self.milestones = {m.id: m for m in milestones}

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self._transactions.get(transaction_id)

    async def list_active_transactions(self) -> list[Transaction]:
        return [t for t in self._transactions.values() if t.is_active]

    async def list_milestones(self, transaction_id: str) -> list[Milestone]:
        return [m for m in self.milestones.values() if m.transaction_id == transaction_id]

    async def set_escalation_level(self, milestone_id: str, level: int) -> None:
        existing = self.milestones[milestone_id]
        self.milestones[milestone_id] = existing.model_copy(update={"escalation_level": level})


class FakeApprovalRepo:
    def __init__(self) -> None:
        self.rows: dict[str, ApprovalRequest] = {}

    async def create(self, approval: ApprovalRequest) -> None:
        self.rows[approval.id] = approval

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self.rows.get(approval_id)

    async def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        rows = list(self.rows.values())
        return [r for r in rows if status is None or r.status == status]

    async def mark_decided(
        self, approval_id: str, status: ApprovalStatus, decided_by: str, decided_at: datetime
    ) -> None:
        row = self.rows.get(approval_id)
        if row is not None:
            self.rows[approval_id] = row.model_copy(
                update={"status": status, "decided_by": decided_by, "decided_at": decided_at}
            )


def make_engine(
    workflow: Workflow, sessions: BaseSessionService | None = None
) -> tuple[AdkWorkflowEngine, BaseSessionService]:
    sessions = sessions or build_session_service(None)
    runner = Runner(
        app=App(
            name=workflow.name,
            root_agent=workflow,
            resumability_config=ResumabilityConfig(is_resumable=True),
        ),
        session_service=sessions,
    )
    return AdkWorkflowEngine({workflow.name: runner}, sessions, FakeApprovalRepo()), sessions


async def final_state(
    sessions: BaseSessionService, workflow: str, thread_id: str
) -> dict[str, Any]:
    session = await sessions.get_session(app_name=workflow, user_id=USER_ID, session_id=thread_id)
    assert session is not None
    return dict(session.state)
