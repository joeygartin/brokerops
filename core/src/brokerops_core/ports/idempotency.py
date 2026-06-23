"""Persistence port for write-tool idempotency.

Every write that crosses the MCP boundary claims a key through this port before it
runs and records its result after; a replay of the same key short-circuits. The
API supplies SQL and in-memory implementations, and the idempotency wrappers in
`core.services.idempotency` depend only on this Protocol — never on a concrete repo.
Engine-agnostic by construction: both orchestration engines dedupe identically
through the same port (architecture rule #5), the same seam the audit-ledger uses.
"""

from typing import Protocol

from brokerops_core.models.idempotency import IdempotencyClaim


class IdempotencyStore(Protocol):
    async def begin(self, key: str, *, workflow_run_id: str, tool: str) -> IdempotencyClaim:
        """Atomically claim `key`. NEW if this caller won it (proceed with the write);
        otherwise PENDING (a prior attempt is unfinished) or COMPLETED (result attached).
        """
        ...

    async def complete(self, key: str, result: str) -> None:
        """Record the original result against `key`, flipping it to COMPLETED."""
        ...
