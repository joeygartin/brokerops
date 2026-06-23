"""Rules for opening a transaction: deterministic id, date validation, schedule.

Keeps the API route thin (rule 3) — the route owns persistence, idempotency, and
HTTP; the rules for what a freshly opened transaction looks like live here.
"""

import hashlib
from collections.abc import Sequence
from datetime import date

from brokerops_core.models.milestone import Milestone, MilestoneTemplate
from brokerops_core.models.transaction import Transaction, TransactionParty, TransactionStage
from brokerops_core.services.milestone_schedule import DEFAULT_TIMELINE, generate_milestones

# Mirrors the String(36) listing_key columns in the persistence schema. A longer
# key would overflow on Postgres, so it is rejected at this boundary, not at write.
LISTING_KEY_MAX_LENGTH = 36


def transaction_id_for_listing(listing_key: str) -> str:
    """Deterministic, bounded transaction id for a listing.

    Derived (not the raw listing key) so it always fits the 36-char id columns —
    including the longer milestone ids built from it — regardless of how long the
    MLS listing key is. Deterministic so opening the same listing twice collides
    on the primary key, which is what makes the operation idempotent per listing.
    """
    digest = hashlib.sha256(listing_key.encode()).hexdigest()[:16]
    return f"TXN-{digest}"


def build_open_transaction(
    listing_key: str,
    contract_date: date,
    close_date: date | None = None,
    parties: Sequence[TransactionParty] = (),
    template: Sequence[MilestoneTemplate] = DEFAULT_TIMELINE,
) -> tuple[Transaction, list[Milestone]]:
    """Validate escrow inputs and build the transaction + milestone timeline.

    Raises ValueError on inconsistent dates: a close before the contract, a
    timeline that needs a close date but none was given, or a generated milestone
    that falls outside the contract→close window. The caller maps it to 422.
    """
    if len(listing_key) > LISTING_KEY_MAX_LENGTH:
        raise ValueError(
            f"listing_key exceeds {LISTING_KEY_MAX_LENGTH} characters ({len(listing_key)})"
        )
    if close_date is not None and close_date < contract_date:
        raise ValueError(f"close_date {close_date} is before contract_date {contract_date}")

    transaction = Transaction(
        id=transaction_id_for_listing(listing_key),
        listing_key=listing_key,
        stage=TransactionStage.UNDER_CONTRACT,
        parties=list(parties),
        contract_date=contract_date,
        close_date=close_date,
    )
    milestones = generate_milestones(transaction, template)

    for milestone in milestones:
        if milestone.due_date < contract_date:
            raise ValueError(
                f"milestone '{milestone.title}' due {milestone.due_date} is before "
                f"contract_date {contract_date}"
            )
        if close_date is not None and milestone.due_date > close_date:
            raise ValueError(
                f"milestone '{milestone.title}' due {milestone.due_date} is after "
                f"close_date {close_date}"
            )
    return transaction, milestones
