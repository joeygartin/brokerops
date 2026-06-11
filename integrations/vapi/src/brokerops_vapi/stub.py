"""Vapi stub — a recorded-response double of the Vapi endpoints we use.

Demo mode runs with zero credentials. On POST /call it stores a "completed"
call with a recorded transcript and, when WEBHOOK_URL is set, immediately
fires a Vapi-shaped end-of-call-report webhook at the api — so the demo's
call → webhook → workflow chain runs without any real telephony.

Transcripts are fully synthetic. Scenario selection: metadata.scenario
("hot" | "cool") or alternating by call order.
"""

import os
from itertools import count
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException

RECORDED_TRANSCRIPTS = {
    "hot": (
        "We loved the kitchen and the backyard was perfect for the kids. "
        "Honestly we are ready to write an offer if the sellers are flexible on the "
        "closing date. Our budget is between four fifty and five twenty five, so it works."
    ),
    "cool": (
        "It was a nice house but it felt overpriced for the neighborhood. "
        "The bathroom is dated and needs updating. We are going to keep looking."
    ),
}


def create_stub_app() -> FastAPI:
    calls: dict[str, dict[str, Any]] = {}
    ids = count(7000)
    scenario_cycle = count()

    app = FastAPI(title="Vapi stub")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    async def fire_end_of_call_webhook(call: dict[str, Any]) -> None:
        webhook_url = os.environ.get("WEBHOOK_URL", "")
        if not webhook_url:
            return
        headers = {}
        secret = os.environ.get("VAPI_WEBHOOK_SECRET", "")
        if secret:
            headers["x-vapi-secret"] = secret
        payload = {
            "message": {
                "type": "end-of-call-report",
                "endedReason": "customer-ended-call",
                "call": {"id": call["id"], "metadata": call["metadata"]},
                "artifact": {"transcript": call["artifact"]["transcript"]},
            }
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(webhook_url, json=payload, headers=headers)
        except httpx.HTTPError:
            pass  # demo stub: the call record still exists for polling via GET /call

    @app.post("/call", status_code=201)
    async def create_call(body: dict[str, Any]) -> dict[str, Any]:
        metadata = body.get("metadata") or {}
        scenario = metadata.get("scenario")
        if scenario not in RECORDED_TRANSCRIPTS:
            scenario = "hot" if next(scenario_cycle) % 2 == 0 else "cool"
        call_id = f"call-{next(ids)}"
        call = {
            "id": call_id,
            "assistantId": body.get("assistantId", ""),
            "status": "ended",
            "endedReason": "customer-ended-call",
            "metadata": metadata,
            "artifact": {"transcript": RECORDED_TRANSCRIPTS[scenario]},
        }
        calls[call_id] = call
        await fire_end_of_call_webhook(call)
        return {"id": call_id, "status": "queued"}

    @app.get("/call/{call_id}")
    async def get_call(call_id: str) -> dict[str, Any]:
        call = calls.get(call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="call not found")
        return call

    return app


app = create_stub_app()
