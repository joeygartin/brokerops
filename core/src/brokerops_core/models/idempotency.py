from enum import StrEnum

from pydantic import BaseModel


class ClaimStatus(StrEnum):
    """The state of an idempotency key when a write tries to claim it.

    NEW is returned only on a fresh claim (this caller won the key and should
    perform the side effect). PENDING and COMPLETED are persisted states an earlier
    attempt left behind: PENDING means a prior attempt claimed the key but never
    recorded a result (the mid-write-crash window); COMPLETED carries the original
    result so a replay returns it without repeating the side effect.
    """

    NEW = "new"
    PENDING = "pending"
    COMPLETED = "completed"


class IdempotencyClaim(BaseModel):
    """The outcome of attempting to claim an idempotency key for a write."""

    status: ClaimStatus
    # The original result (a string for string-returning tools, or a model's
    # model_dump_json() for model-returning ones), present only when COMPLETED.
    result: str | None = None
