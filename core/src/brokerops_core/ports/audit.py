"""Persistence port for the action audit-ledger.

Every external write the system performs becomes a MutationRecord row through this
port; the API's SQL and in-memory repos implement it, and the recording wrappers in
`core.services.audit` depend only on it — never on a concrete repo. Engine-agnostic
by construction: both orchestration engines record identically through the same port.
"""

from typing import Protocol

from brokerops_core.models.mutation import MutationRecord


class AuditLog(Protocol):
    async def record(self, record: MutationRecord) -> None: ...

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]: ...


class TransactionAuditLog(AuditLog, Protocol):
    """The ledger's per-transaction read surface (BOP-027).

    A narrower capability than plain recording — only the audit route needs it, so
    it stays off the base port that the recording seam's many callers depend on.
    The production stores implement it; the route depends on this protocol.
    """

    async def list_for_transaction(
        self, transaction_id: str, limit: int = 200
    ) -> list[MutationRecord]:
        """The ledger entries belonging to one transaction, newest first.

        Every write from a transaction-scoped run is stamped with the deal id
        (`MutationRecord.transaction_id`) at record time, so this is a single exact
        match on that field — complete regardless of whether the run raised an
        approval, and never bounded by a scan window.
        """
        ...
