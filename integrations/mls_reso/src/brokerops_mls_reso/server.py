"""Mock RESO Web API — an OData subset over synthetic seed data.

Field names follow the RESO Data Dictionary. Swapping to a live MLS feed is a
base-URL + auth change in the consuming adapter; this server's surface is the
contract.
"""

import json
from importlib import resources
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request

from brokerops_mls_reso.odata import ODataError, apply_query

SeedData = dict[str, list[dict[str, Any]]]


def load_seed() -> SeedData:
    text = resources.files("brokerops_mls_reso").joinpath("seed/listings.json").read_text()
    return cast(SeedData, json.loads(text))


def create_app(seed: SeedData | None = None) -> FastAPI:
    data = seed if seed is not None else load_seed()
    properties = data["Property"]
    media = data["Media"]

    app = FastAPI(title="mock RESO Web API")

    def run_query(records: list[dict[str, Any]], request: Request) -> list[dict[str, Any]]:
        try:
            return apply_query(records, request.query_params)
        except ODataError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/odata/Property")
    async def list_properties(request: Request) -> dict[str, Any]:
        value = run_query(properties, request)
        return {"@odata.context": "$metadata#Property", "value": value}

    @app.get("/odata/Property('{listing_key}')")
    async def get_property(listing_key: str) -> dict[str, Any]:
        record = next((p for p in properties if p["ListingKey"] == listing_key), None)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Property {listing_key!r} not found")
        return {"@odata.context": "$metadata#Property/$entity", **record}

    @app.get("/odata/Media")
    async def list_media(request: Request) -> dict[str, Any]:
        value = run_query(media, request)
        return {"@odata.context": "$metadata#Media", "value": value}

    return app


app = create_app()
