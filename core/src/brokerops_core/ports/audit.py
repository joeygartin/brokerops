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
