"""build_open_transaction — id derivation/bounding + escrow-date validation (BOP-004 r2)."""

from datetime import date

import pytest

from brokerops_core.services.transaction_open import (
    build_open_transaction,
    transaction_id_for_listing,
)

CONTRACT = date(2026, 6, 1)
CLOSE = date(2026, 7, 1)


def test_id_is_deterministic_and_bounded() -> None:
    # A long MLS listing key must still yield ids that fit the 36-char id columns —
    # including the longer milestone ids derived from the transaction id.
    long_key = "RM" + "9" * 60
    assert transaction_id_for_listing(long_key) == transaction_id_for_listing(long_key)
    txn, milestones = build_open_transaction(long_key, CONTRACT, CLOSE)
    assert txn.id == transaction_id_for_listing(long_key)
    assert len(txn.id) <= 36
    assert milestones and all(len(m.id) <= 36 for m in milestones)


def test_distinct_listings_get_distinct_ids() -> None:
    assert transaction_id_for_listing("RM-A") != transaction_id_for_listing("RM-B")


def test_valid_escrow_builds_full_timeline_within_window() -> None:
    txn, milestones = build_open_transaction("RM-1", CONTRACT, CLOSE)
    assert txn.contract_date == CONTRACT
    assert txn.close_date == CLOSE
    assert len(milestones) == 5
    assert all(CONTRACT <= m.due_date <= CLOSE for m in milestones)


def test_close_before_contract_raises() -> None:
    with pytest.raises(ValueError, match="before contract_date"):
        build_open_transaction("RM-1", CONTRACT, date(2026, 5, 15))


def test_close_too_soon_for_timeline_raises() -> None:
    # DEFAULT_TIMELINE financing is contract + 21; a ~10-day escrow can't fit it.
    with pytest.raises(ValueError, match="after close_date"):
        build_open_transaction("RM-1", CONTRACT, date(2026, 6, 11))


def test_before_close_step_without_close_date_raises() -> None:
    with pytest.raises(ValueError, match="no close_date"):
        build_open_transaction("RM-1", CONTRACT, None)
