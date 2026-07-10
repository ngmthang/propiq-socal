"""
    PropIQ - FastAPI Serving Layer

    Wires together:
        - data_layer: Postgres models via SQLAlchemy session dependency
        - ml_layer: InferenceEngine (AVM + LSTM + DealAnalyzer) + MLScheduler
        - This layer: routers, Pydantic schemas, API-key auth, docs

    Run locally:
        uvicorn api_layer.main:app --reload --port 8000

    Run in Docker:
        see docker-compose.yml / Dockerfile.worker.api

    @author Minh Thang Nguyen
    @version July 10, 2026
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from .core.auth import APIKeyMiddleware
from .core.config import settings
from .dependencies.ml import MLState
from .routers import properties, search, market

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load ML models + start scheduler. Shutdown: clear teardown."""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION} [{settings.ENVIRONMENT}]")

    ml_state = MLState()
    app.state.ml = ml_state

    try:
        ml_state.load()
    except Exception:
        logger.exception(
            "Failed to load ML models on startup - the API will run, but "
            "valuation/analysis/market endpoints will 503 until this is fixed."
        )

    yield

    logger.info("Shutting down PropIQ API...")
    ml_state.shutdown()

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Real-estate valuation, forecasting, and deal-analysis API for SoCal properties.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API-key gate (belt-and-suspenders alongside the per-router dependency)
    app.add_middleware(APIKeyMiddleware)

    # Routers
    app.include_router(properties.router)
    app.include_router(search.router)
    app.include_router(market.router)

    # Health & meta
    @app.get("/", tags=["meta"])
    def root():
        return {
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "ok"
        }

    @app.get("/health", tags=["meta"])
    def health():
        ml_state: MLState = app.state.ml
        return {
            "status": "ok",
            "engine_loaded": ml_state.engine is not None,
            "scheduler_running": ml_state.scheduler is not None,
        }

    # Error handling
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors()},
        )

    @app.exception_handler(RuntimeError)
    async def runtime_exception_handler(request: Request, exc: RuntimeError):
        # e.g. InferenceEngine not initialized yet
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
        )

    return app

app = create_app()