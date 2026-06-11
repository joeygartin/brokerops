"""The HITL pause, in ADK terms.

A workflow node yields a RequestInput to suspend the run; the engine turns
its payload into an ApprovalRequest row and later resumes the invocation
with the decision, which the rerun node finds in ctx.resume_inputs.
"""

from typing import Any

from google.adk.events.request_input import RequestInput


def request_input(interrupt_id: str, payload: dict[str, Any]) -> RequestInput:
    return RequestInput(
        interrupt_id=interrupt_id, payload=payload, message=None, response_schema=None
    )
