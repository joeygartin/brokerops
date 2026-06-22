"""FollowUpBoss stub — a recorded-shape double of the FUB endpoints we use.

Demo mode and contract tests run against this with zero credentials. It
mirrors the real API's request/response shapes (people, tasks, notes, calls)
over in-memory state, with fully synthetic seed contacts.
"""

from itertools import count
from typing import Any

from fastapi import FastAPI, HTTPException

SEED_PEOPLE: list[dict[str, Any]] = [
    {
        "id": 101,
        "firstName": "Jordan",
        "lastName": "Pike",
        "name": "Jordan Pike",
        "stage": "Lead",
        "emails": [{"value": "jordan.pike@example.test"}],
        "phones": [{"value": "+1-555-0101"}],
    },
    {
        "id": 102,
        "firstName": "Casey",
        "lastName": "Romero",
        "name": "Casey Romero",
        "stage": "Hot Prospect",
        "emails": [{"value": "casey.romero@example.test"}],
        "phones": [{"value": "+1-555-0102"}],
    },
    {
        "id": 103,
        "firstName": "Alex",
        "lastName": "Yuen",
        "name": "Alex Yuen",
        "stage": "Lead",
        "emails": [{"value": "alex.yuen@example.test"}],
        "phones": [],
    },
]


def create_stub_app() -> FastAPI:
    people: dict[int, dict[str, Any]] = {p["id"]: dict(p) for p in SEED_PEOPLE}
    tasks: dict[int, dict[str, Any]] = {}
    notes: dict[int, dict[str, Any]] = {}
    calls: dict[int, dict[str, Any]] = {}
    ids = count(1000)

    app = FastAPI(title="FollowUpBoss stub")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/people")
    async def search_people(q: str = "", limit: int = 20) -> dict[str, Any]:
        needle = q.lower()

        def matches(person: dict[str, Any]) -> bool:
            if not needle:
                return True
            haystack = " ".join(
                [person.get("name", "")] + [e["value"] for e in person.get("emails", [])]
            ).lower()
            return needle in haystack

        found = [p for p in people.values() if matches(p)][:limit]
        return {"people": found}

    @app.get("/people/{person_id}")
    async def get_person(person_id: str) -> dict[str, Any]:
        # Take the id as a string and 404 on anything unknown — including a
        # non-numeric id — so the stub matches real FUB (an int path type would
        # 422 "nope" before the handler, surfacing as a 500 upstream).
        person = people.get(int(person_id)) if person_id.isdigit() else None
        if person is None:
            raise HTTPException(status_code=404, detail="person not found")
        return person

    @app.post("/people", status_code=201)
    async def create_person(body: dict[str, Any]) -> dict[str, Any]:
        person_id = next(ids)
        person = {
            "id": person_id,
            "firstName": body.get("firstName", ""),
            "lastName": body.get("lastName", ""),
            "name": f"{body.get('firstName', '')} {body.get('lastName', '')}".strip(),
            "stage": "Lead",
            "source": body.get("source", ""),
            "emails": body.get("emails", []),
            "phones": body.get("phones", []),
        }
        people[person_id] = person
        return person

    @app.post("/tasks", status_code=201)
    async def create_task(body: dict[str, Any]) -> dict[str, Any]:
        task_id = next(ids)
        task = {"id": task_id, **body}
        tasks[task_id] = task
        return task

    @app.get("/tasks")
    async def list_tasks() -> dict[str, Any]:
        return {"tasks": list(tasks.values())}

    @app.post("/notes", status_code=201)
    async def create_note(body: dict[str, Any]) -> dict[str, Any]:
        note_id = next(ids)
        notes[note_id] = {"id": note_id, **body}
        return notes[note_id]

    # Real FUB rejects calls missing phone/personId/isIncoming, and constrains
    # outcome to a fixed vocabulary; the stub enforces the same so the contract
    # tests catch payload drift instead of letting it surface in production.
    _CALL_OUTCOMES = {
        "Interested",
        "Not Interested",
        "Left Message",
        "No Answer",
        "Busy",
        "Bad Number",
    }

    @app.post("/calls", status_code=201)
    async def log_call(body: dict[str, Any]) -> dict[str, Any]:
        for field in ("personId", "phone", "isIncoming"):
            if body.get(field) is None:
                raise HTTPException(
                    status_code=400, detail=f"Missing required field in the request body: {field}."
                )
        if "outcome" in body and body["outcome"] not in _CALL_OUTCOMES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid field value, field name: outcome, allowed values: "
                    f"{', '.join(sorted(_CALL_OUTCOMES))}"
                ),
            )
        call_id = next(ids)
        calls[call_id] = {"id": call_id, **body}
        return calls[call_id]

    return app


app = create_stub_app()
