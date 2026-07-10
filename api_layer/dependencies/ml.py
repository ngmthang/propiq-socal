"""
    PropIQ - ML Dependency Wiring
    Loads the InferenceEngine (AVM + LSTM + DealAnalyzer) once at process startup
    and hands it out to routes as a FastAPI dependency. Also owns the MLScheudler
    lifecycle (start on startup, shutdown on app shutdown).

    @author Minh Thang Nguyen
    @version July 9, 2026
"""

from __future__ import annotations

from loguru import logger
from fastapi import Request

from ml_layer.inference.engine import InferenceEngine
from ml_layer.training.scheduler import MLScheduler
from ..core.config import settings

class MLState:
    """Holds process-wide singletons. Attached to 'app.state.ml'."""

    def __init__(self) -> None:
        self.engine: InferenceEngine | None = None
        self.scheduler: MLScheduler | None = None

    def load(self) -> None:
        logger.info("Loading InferenceEngine (AVM + LSTM + DealAnalyzer)...")
        self.engine = InferenceEngine.from_paths(
            avm_path=settings.AVM_MODEL_PATH,
            lstm_path=settings.LSTM_MODEL_PATH,
            enable_ai=settings.ENABLE_AI_ANALYSIS,
            anthropic_key=settings.ANTHROPIC_API_KEY,
        )
        logger.info("InferenceEngine ready.")

        if settings.ENABLE_ML_SCHEDULER:
            logger.info("Starting MLScheduler (retrain jobs)...")
            self.scheduler = MLScheduler(
                db_url=settings.DATABASE_URL,
                avm_cron=settings.AVM_RETRAIN_CRON,
                lstm_cron=settings.LSTM_RETRAIN_CRON,
            )
            self.scheduler.start()
            logger.info("MLScheduler started.")

    def shutdown(self) -> None:
        if self.scheduler is not None:
            logger.info("Shutting dow MLScheduler...")
            self.scheduler.shutdown()
        self.engine = None
        self.scheduler = None


def get_inference_engine(request: Request) -> InferenceEngine:
    """
    FastAPI dependency: returns the shared InferenceEngine instance/
    Raises a clear 503 if called before startup finishes loading models.
    """
    ml_state: MLState = request.app.state.ml
    if ml_state.engine is None:
        raise RuntimeError(
            "InferenceEngine not initialized - app startup may have failed."
        )
    return ml_state.engine