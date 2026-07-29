"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.redis import close_redis
from app.database import close_db, init_db
from app.routers import admin as admin_router
from app.routers import approvals, exports, integrations, invoices, n8n, webhooks
from app.services.ingestion import IngestionError

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    settings.validate_security()
    logger.info("Starting Invoice Processor API...")
    logger.info("Storage backend: %s", settings.storage_backend)
    logger.info("LLM provider: %s", settings.llm_provider)

    # Initialize database
    await init_db()
    logger.info("Database tables initialized")

    yield

    # Shutdown
    await close_db()
    await close_redis()
    logger.info("Invoice Processor API shut down")


app = FastAPI(
    title=settings.app_name,
    description="AI-powered invoice processing automation system",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(IngestionError)
async def ingestion_error_handler(request: Request, exc: IngestionError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
    )


app.include_router(admin_router.router)
app.include_router(invoices.router)
app.include_router(exports.router)
app.include_router(webhooks.router)
app.include_router(integrations.router)
app.include_router(approvals.approvals_router)
app.include_router(approvals.purchase_orders_router)
app.include_router(approvals.vendors_router)
app.include_router(n8n.router)


# ─── Health Check ────────────────────────────────────────────────


@app.get("/health", tags=["System"])
async def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.1.0",
        "storage": settings.storage_backend,
        "llm_provider": settings.llm_provider,
    }


@app.get("/", tags=["System"])
async def root() -> dict:
    """Root endpoint."""
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }


static_dir = Path(__file__).parent.parent / "storage"
static_dir.mkdir(exist_ok=True)

try:
    app.mount("/storage", StaticFiles(directory=str(static_dir)), name="storage")
except Exception:
    pass
