"""Durable workflow state via ADK session services.

With a database URL, sessions live in Postgres (ADK manages its own
`sessions` / `events` / `*_states` tables) so a HITL pause survives process
restarts, deploys, and Cloud Run cold starts — the same guarantee the
LangGraph checkpointer provides. Without one, everything is in memory.
"""

from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService


def _sqlalchemy_url(database_url: str) -> str:
    """Normalize a plain postgres DSN to SQLAlchemy's psycopg dialect."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def build_session_service(database_url: str | None) -> BaseSessionService:
    if database_url:
        return DatabaseSessionService(_sqlalchemy_url(database_url))
    return InMemorySessionService()  # type: ignore[no-untyped-call]


async def close_session_service(service: BaseSessionService) -> None:
    # Only the database-backed service owns a connection pool to release;
    # the base class has no close().
    if isinstance(service, DatabaseSessionService):
        await service.close()
