"""Synthetic demo transactions, dated relative to today so the demo always
shows one overdue inspection, one near deadline, and one external blocker.
Some milestones declare an expected document (BOP-021) so the Documents tab
has an attached-vs-expected story out of the box — the matching synthetic
paperwork is seeded in the bundled Drive stub under each listing key."""

from datetime import date, timedelta

from brokerops_core.models.document import DocumentKind
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionParty, TransactionStage


def demo_transactions(today: date) -> list[tuple[Transaction, list[Milestone]]]:
    def milestone(
        milestone_id: str,
        transaction_id: str,
        milestone_type: MilestoneType,
        title: str,
        days_out: int,
        owner: str,
        blocked_reason: str | None = None,
        expected_document: DocumentKind | None = None,
    ) -> Milestone:
        return Milestone(
            id=milestone_id,
            transaction_id=transaction_id,
            type=milestone_type,
            title=title,
            due_date=today + timedelta(days=days_out),
            owner=owner,
            blocked_reason=blocked_reason,
            expected_document=expected_document,
        )

    txn_1 = Transaction(
        id="TXN-1001",
        listing_key="RM1004",
        stage=TransactionStage.CONTINGENCIES,
        parties=[
            TransactionParty(role="buyer", name="Jordan Pike", contact_id="101"),
            TransactionParty(role="listing_agent", name="Dana Whitfield"),
        ],
        contract_date=today - timedelta(days=10),
        close_date=today + timedelta(days=25),
    )
    txn_1_milestones = [
        milestone(
            "MS-1001-INS",
            txn_1.id,
            MilestoneType.INSPECTION,
            "Home inspection",
            -2,
            "Dana Whitfield",
            expected_document=DocumentKind.INSPECTION_REPORT,
        ),
        milestone("MS-1001-APP", txn_1.id, MilestoneType.APPRAISAL, "Appraisal", 10, "Lender"),
        milestone("MS-1001-FIN", txn_1.id, MilestoneType.FINANCING, "Loan approval", 15, "Lender"),
        milestone("MS-1001-CLO", txn_1.id, MilestoneType.CLOSING, "Closing", 25, "Escrow"),
    ]

    txn_2 = Transaction(
        id="TXN-1002",
        listing_key="RM1010",
        stage=TransactionStage.UNDER_CONTRACT,
        parties=[
            TransactionParty(role="buyer", name="Casey Romero", contact_id="102"),
            TransactionParty(role="listing_agent", name="Dana Whitfield"),
        ],
        contract_date=today - timedelta(days=5),
        close_date=today + timedelta(days=30),
    )
    txn_2_milestones = [
        milestone(
            "MS-1002-INS",
            txn_2.id,
            MilestoneType.INSPECTION,
            "Home inspection",
            2,
            "Dana Whitfield",
            expected_document=DocumentKind.INSPECTION_REPORT,
        ),
        milestone("MS-1002-APP", txn_2.id, MilestoneType.APPRAISAL, "Appraisal", 12, "Lender"),
        milestone("MS-1002-CLO", txn_2.id, MilestoneType.CLOSING, "Closing", 30, "Escrow"),
    ]

    txn_3 = Transaction(
        id="TXN-1003",
        listing_key="RM1002",
        stage=TransactionStage.CLEAR_TO_CLOSE,
        parties=[
            TransactionParty(role="buyer", name="Alex Yuen", contact_id="103"),
            TransactionParty(role="listing_agent", name="Marcus Bell"),
        ],
        contract_date=today - timedelta(days=20),
        close_date=today + timedelta(days=12),
    )
    txn_3_milestones = [
        milestone(
            "MS-1003-FIN",
            txn_3.id,
            MilestoneType.FINANCING,
            "Final loan docs",
            12,
            "Lender",
            blocked_reason="Awaiting lender underwriting update",
            expected_document=DocumentKind.LOAN_APPROVAL,
        ),
        milestone("MS-1003-CLO", txn_3.id, MilestoneType.CLOSING, "Closing", 12, "Escrow"),
    ]

    return [
        (txn_1, txn_1_milestones),
        (txn_2, txn_2_milestones),
        (txn_3, txn_3_milestones),
    ]
