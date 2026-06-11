from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from brokerops_api.routes.listings import router as listings_router

app = FastAPI(title="brokerops api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(listings_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "brokerops api", "phase": "1"}
