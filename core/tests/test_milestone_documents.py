"""Milestone expected-document tie-in (BOP-021).

A template step declares the document kind that proves it; the engine *reports*
attached-vs-expected — read-only, no workflow routing on it.
"""

from datetime import UTC, date, datetime

from brokerops_core.models.document import Document, DocumentKind
from brokerops_core.models.files import FileRef
from brokerops_core.models.milestone import (
    DueRule,
    Milestone,
    MilestoneTemplate,
    MilestoneType,
)
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.services.milestone_engine import expected_document_satisfied
from brokerops_core.services.milestone_schedule import DEFAULT_TIMELINE, generate_milestones


def _milestone(expected: DocumentKind | None) -> Milestone:
    return Milestone(
        id="m1",
        transaction_id="tx1",
        type=MilestoneType.INSPECTION,
        title="Home inspection",
        due_date=date(2026, 7, 10),
        expected_document=expected,
    )


def _document(
    kind: DocumentKind, milestone_id: str | None = None, transaction_id: str = "tx1"
) -> Document:
    return Document(
        id=f"d-{kind.value}",
        transaction_id=transaction_id,
        milestone_id=milestone_id,
        kind=kind,
        title=kind.value,
        file=FileRef(file_id="f1", name=f"{kind.value}.pdf"),
        created_at=datetime.now(UTC),
    )


def test_no_expectation_reports_none() -> None:
    assert expected_document_satisfied(_milestone(None), []) is None
    assert (
        expected_document_satisfied(_milestone(None), [_document(DocumentKind.DISCLOSURES)]) is None
    )


def test_unsatisfied_without_matching_kind() -> None:
    milestone = _milestone(DocumentKind.INSPECTION_REPORT)
    assert expected_document_satisfied(milestone, []) is False
    # a different kind on the same transaction does not satisfy
    assert expected_document_satisfied(milestone, [_document(DocumentKind.DISCLOSURES)]) is False


def test_satisfied_by_transaction_level_attachment() -> None:
    milestone = _milestone(DocumentKind.INSPECTION_REPORT)
    attached = [_document(DocumentKind.INSPECTION_REPORT)]  # milestone_id=None
    assert expected_document_satisfied(milestone, attached) is True


def test_satisfied_by_own_milestone_attachment_but_not_anothers() -> None:
    milestone = _milestone(DocumentKind.INSPECTION_REPORT)
    own = [_document(DocumentKind.INSPECTION_REPORT, milestone_id="m1")]
    other = [_document(DocumentKind.INSPECTION_REPORT, milestone_id="m2")]
    assert expected_document_satisfied(milestone, own) is True
    # pinned to a different milestone — does not satisfy this one
    assert expected_document_satisfied(milestone, other) is False


def test_template_expectation_flows_into_generated_milestones() -> None:
    txn = Transaction(
        id="TXN-1",
        listing_key="L1",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=date(2026, 6, 1),
        close_date=date(2026, 7, 15),
    )
    generated = generate_milestones(txn, DEFAULT_TIMELINE)
    by_type = {m.type: m for m in generated}
    assert by_type[MilestoneType.INSPECTION].expected_document is DocumentKind.INSPECTION_REPORT
    assert by_type[MilestoneType.FINANCING].expected_document is DocumentKind.LOAN_APPROVAL
    assert by_type[MilestoneType.CLOSING].expected_document is None


def test_custom_template_step_can_expect_a_document() -> None:
    txn = Transaction(
        id="TXN-2",
        listing_key="L2",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=date(2026, 6, 1),
    )
    template = (
        MilestoneTemplate(
            type=MilestoneType.CUSTOM,
            title="Collect signed disclosures",
            rule=DueRule.AFTER_CONTRACT,
            days=5,
            expected_document=DocumentKind.DISCLOSURES,
        ),
    )
    (milestone,) = generate_milestones(txn, template)
    assert milestone.expected_document is DocumentKind.DISCLOSURES
