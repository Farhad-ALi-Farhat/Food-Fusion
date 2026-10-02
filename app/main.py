import logging

from fastapi import FastAPI

from app import models  # noqa: F401 — register all tables for create_all
from app.database import Base, engine
from app.routers import webhook

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Food Fusion — Order-Placement Assistant")

app.include_router(webhook.router)


@app.on_event("startup")
def on_startup():
    # Dev convenience — swap for Alembic migrations once the schema stabilizes.
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}
