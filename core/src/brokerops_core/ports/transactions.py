from typing import Protocol

from brokerops_core.models.milestone import Milestone
from brokerops_core.models.transaction import Transaction


class TransactionAlreadyExists(Exception):
    """Raised by `create_transaction` when a transaction with that id already exists.

    Lets a caller make "open" idempotent under a concurrent retry: catch this,
    re-read, and return the winner instead of surfacing a 500.
    """


class TransactionStore(Protocol):
    """Domain persistence boundary for transactions and milestones.

    Implemented over Postgres in the api service and in memory for tests;
    workflows and services never see SQL.
    """

    async def create_transaction(
        self, transaction: Transaction, milestones: list[Milestone], /
    ) -> None:
        """Persist a new transaction and its milestone timeline together.

        Raises `TransactionAlreadyExists` if the transaction id is already present
        (the atomic claim that makes opening idempotent under a race).

        Positional-only: implementations use their own parameter names (the SQL
        store's `milestones` table would otherwise shadow the argument).
        """
        ...

    async def get_transaction(self, transaction_id: str) -> Transaction | None: ...

    async def list_active_transactions(self) -> list[Transaction]: ...

    async def list_milestones(self, transaction_id: str) -> list[Milestone]: ...

    async def set_escalation_level(self, milestone_id: str, level: int) -> None: ...
