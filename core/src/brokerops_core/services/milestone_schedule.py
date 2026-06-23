"""Generate a transaction's milestone timeline from a per-client template.

Date math only. The template is plain data (a sequence of `MilestoneTemplate`),
so a client's timeline becomes per-client config in the next onboarding iteration
without touching this generator. Pairs with `milestone_engine`, which assesses the
milestones this produces.

V1 onboarding model (BOP-004): `DEFAULT_TIMELINE` is hand-coded; replace it per
client from their intake (`docs/CLIENT_ONBOARDING.md` §4).
"""

from collections.abc import Sequence
from datetime import date, timedelta

from brokerops_core.models.milestone import (
    DueRule,
    Milestone,
    MilestoneTemplate,
    MilestoneType,
)
from brokerops_core.models.transaction import Transaction

# A standard residential-purchase timeline. Illustrative defaults — real values
# come from the brokerage's intake. Closing anchors on the close date; the
# inspection/appraisal/financing steps count forward from contract acceptance.
DEFAULT_TIMELINE: tuple[MilestoneTemplate, ...] = (
    MilestoneTemplate(
        type=MilestoneType.INSPECTION,
        title="Home inspection",
        owner="Buyer's agent",
        rule=DueRule.AFTER_CONTRACT,
        days=10,
    ),
    MilestoneTemplate(
        type=MilestoneType.APPRAISAL,
        title="Appraisal",
        owner="Lender",
        rule=DueRule.AFTER_CONTRACT,
        days=17,
    ),
    MilestoneTemplate(
        type=MilestoneType.FINANCING,
        title="Loan approval",
        owner="Lender",
        rule=DueRule.AFTER_CONTRACT,
        days=21,
    ),
    MilestoneTemplate(
        type=MilestoneType.CUSTOM,
        title="Final walkthrough",
        owner="Buyer's agent",
        rule=DueRule.BEFORE_CLOSE,
        days=1,
    ),
    MilestoneTemplate(
        type=MilestoneType.CLOSING,
        title="Closing",
        owner="Escrow",
        rule=DueRule.BEFORE_CLOSE,
        days=0,
    ),
)


def _due_date(transaction: Transaction, entry: MilestoneTemplate) -> date:
    if entry.rule is DueRule.AFTER_CONTRACT:
        return transaction.contract_date + timedelta(days=entry.days)
    if transaction.close_date is None:
        raise ValueError(
            f"milestone '{entry.title}' is anchored before close, but transaction "
            f"{transaction.id} has no close_date"
        )
    return transaction.close_date - timedelta(days=entry.days)


def milestone_id(transaction_id: str, index: int, milestone_type: MilestoneType) -> str:
    """Deterministic, collision-free milestone id.

    Deterministic in (transaction, position) so regenerating the same timeline
    yields the same ids — which keeps opening a transaction idempotent (BOP-004 #3).
    """
    return f"{transaction_id}-MS{index:02d}-{milestone_type.value[:3].upper()}"


def generate_milestones(
    transaction: Transaction,
    template: Sequence[MilestoneTemplate] = DEFAULT_TIMELINE,
) -> list[Milestone]:
    """Build the milestone timeline for a transaction from a template.

    Raises ValueError if a `before_close` step is used on a transaction without a
    close date.
    """
    return [
        Milestone(
            id=milestone_id(transaction.id, index, entry.type),
            transaction_id=transaction.id,
            type=entry.type,
            title=entry.title,
            due_date=_due_date(transaction, entry),
            owner=entry.owner,
        )
        for index, entry in enumerate(template, start=1)
    ]
