"""Sierra Interactive stub — a recorded-shape double of the endpoints we use.

Demo mode and contract tests run against this with zero credentials. It mirrors
the documented API's request/response shapes — the ``{"success", "data"}``
envelope, ``errorMessage`` failures, lead get/find, note add, task add — over
in-memory state, with fully synthetic seed leads.
"""

from itertools import count
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SEED_LEADS: list[dict[str, Any]] = [
    {
        "id": 501,
        "firstName": "Morgan",
        "lastName": "Ellis",
        "leadStatus": "New",
        "email": "morgan.ellis@example.test",
        "phone": "(555) 010-0501",
        "source": "SierraApi",
    },
    {
        "id": 502,
        "firstName": "Priya",
        "lastName": "Nair",
        "leadStatus": "Active",
        "email": "priya.nair@example.test",
        "phone": "(555) 010-0502",
        "source": "SierraApi",
    },
    {
        "id": 503,
        "firstName": "Sam",
        "lastName": "Okafor",
        "leadStatus": "Qualify",
        "email": "sam.okafor@example.test",
        "phone": "",
        "source": "SierraApi",
    },
]

# The stub's anchor lead for tasks created without a contact (see adapter);
# any seed lead works — deploys configure their own.
STUB_TASK_ANCHOR_LEAD_ID = "501"
STUB_TASK_ASSIGNEE_ID = 4321

_TASK_TYPES = {"Other", "Email", "PhoneCall", "TextMessage", "OnSiteMessage"}


def _ok(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"success": True, "data": data}, status_code=status_code)


def _error(status_code: int, message: str) -> JSONResponse:
    # Documented failure envelope: HTTP 300-999 + success:false + errorMessage.
    return JSONResponse({"success": False, "errorMessage": message}, status_code=status_code)


def create_stub_app() -> FastAPI:
    leads: dict[int, dict[str, Any]] = {lead["id"]: dict(lead) for lead in SEED_LEADS}
    notes: dict[int, dict[str, Any]] = {}
    tasks: dict[int, dict[str, Any]] = {}
    ids = count(9000)

    app = FastAPI(title="Sierra Interactive stub")

    def find_lead(lead_id_or_email: str) -> dict[str, Any] | None:
        if lead_id_or_email.isdigit():
            return leads.get(int(lead_id_or_email))
        return next((lead for lead in leads.values() if lead["email"] == lead_id_or_email), None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/leads/get/{lead_id_or_email}")
    async def get_lead(lead_id_or_email: str) -> JSONResponse:
        lead = find_lead(lead_id_or_email)
        if lead is None:
            return _error(404, "Lead not found")
        return _ok(lead)

    @app.get("/leads/find")
    async def find_leads(
        name: str = "", email: str = "", phone: str = "", pageSize: int = 100
    ) -> JSONResponse:
        def matches(lead: dict[str, Any]) -> bool:
            full_name = f"{lead['firstName']} {lead['lastName']}".lower()
            if name and name.lower() not in full_name:
                return False
            if email and email.lower() not in lead["email"].lower():
                return False
            if phone and phone not in lead["phone"]:
                return False
            return True

        found = [lead for lead in leads.values() if matches(lead)][:pageSize]
        return _ok(
            {
                "totalRecords": len(found),
                "totalPages": 1,
                "pageSize": pageSize,
                "leads": found,
            }
        )

    @app.post("/leads")
    async def add_lead(request: Request) -> JSONResponse:
        body = await request.json()
        # Real Sierra requires both; the stub enforces the same so contract
        # tests catch payload drift instead of production.
        if not body.get("email"):
            return _error(400, "Invalid email address format")
        if not body.get("password"):
            return _error(400, "Missing required field: password")
        lead_id = next(ids)
        leads[lead_id] = {
            "id": lead_id,
            "firstName": body.get("firstName", ""),
            "lastName": body.get("lastName", ""),
            "leadStatus": body.get("leadStatus", "New"),
            "email": body["email"],
            "phone": body.get("phone", ""),
            "source": body.get("source", "SierraApi"),
        }
        # Registration returns assignment details, not the lead record.
        return _ok(
            {
                "leadId": lead_id,
                "agentUserId": -1,
                "agentUserEmail": "",
                "agentSiteId": -1,
                "updateDate": "2026-01-01T00:00:00.000Z",
            },
            status_code=201,
        )

    @app.post("/leads/{lead_id_or_email}/note")
    async def add_note(lead_id_or_email: str, request: Request) -> JSONResponse:
        if find_lead(lead_id_or_email) is None:
            return _error(404, "Lead not found")
        body = await request.json()
        if not body.get("message"):
            return _error(400, "Missing required field: message")
        note_id = next(ids)
        notes[note_id] = {"id": note_id, "lead": lead_id_or_email, "message": body["message"]}
        return _ok({"id": note_id}, status_code=201)

    @app.post("/leads/{lead_id_or_email}/task")
    async def add_task(lead_id_or_email: str, request: Request) -> JSONResponse:
        lead = find_lead(lead_id_or_email)
        if lead is None:
            return _error(404, "Lead not found")
        body = await request.json()
        task_type = body.get("taskType", "Other")
        if task_type not in _TASK_TYPES:
            return _error(400, f"Invalid field value, field name: taskType, value: {task_type}")
        if body.get("toUserId") is None:
            return _error(400, "Missing required field: toUserId")
        if not body.get("dueOn"):
            return _error(400, "Missing required field: dueOn")
        if task_type == "Other" and not body.get("contents"):
            return _error(400, "Missing required field: contents")
        task_id = next(ids)
        tasks[task_id] = {
            "id": task_id,
            "leadId": lead["id"],
            "toUserId": body["toUserId"],
            "dueOn": body["dueOn"],
            "taskType": task_type,
            "contents": body.get("contents", ""),
            "isFavorite": bool(body.get("isFavorite", False)),
            "dateCreated": "2026-01-01T00:00:00.000Z",
            "dateUpdated": "2026-01-01T00:00:00.000Z",
        }
        return _ok(tasks[task_id], status_code=201)

    @app.get("/leads/{lead_id_or_email}/task")
    async def find_tasks(lead_id_or_email: str) -> JSONResponse:
        lead = find_lead(lead_id_or_email)
        if lead is None:
            return _error(404, "Lead not found")
        found = [t for t in tasks.values() if t["leadId"] == lead["id"]]
        return _ok({"totalRecords": len(found), "tasks": found})

    return app


app = create_stub_app()
