"""Persistence port for the HITL approval spine.

Every workflow pause becomes an ApprovalRequest row through this port; the
API's SQL and in-memory repos implement it, and every orchestration engine
depends on it — never on a concrete repo.
"""

from datetime import datetime
from typing import Protocol

from brokerops_core.models.approval import ApprovalRequest, ApprovalStatus


class ApprovalRepo(Protocol):
    async def create(self, approval: ApprovalRequest) -> None: ...

    async def get(self, approval_id: str) -> ApprovalRequest | None: ...

    async def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]: ...

    async def mark_decided(
        self, approval_id: str, status: ApprovalStatus, decided_by: str, decided_at: datetime
    ) -> None: ...
