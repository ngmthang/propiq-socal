# PropIQ — ML/AI Layer
#
# Imports are lazy (PEP 562 module __getattr__) so that e.g.
# `from ml_layer.training.avm_trainer import AVMTrainer` doesn't require
# torch (LSTMTrainer) or anthropic (DealAnalyzer) to be installed/working.
# Previously this file imported everything eagerly at package-import time,
# so any consumer of the AVM alone would hard-fail if torch failed to load
# (e.g. a broken DLL on Windows) even though the AVM path never uses torch.

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only seen by static type checkers (Pylance/mypy), never executed at
    # runtime, so this doesn't reintroduce the eager torch/anthropic import.
    from .inference.engine import InferenceEngine, FullPropertyAnalysis, ValuationResult, ForecastResult
    from .inference.deal_analyzer import DealAnalyzer, DealAnalysis, PropertyContext
    from .training.avm_trainer import AVMTrainer, AVMConfig
    from .training.lstm_trainer import LSTMTrainer, LSTMConfig
    from .training.scheduler import MLScheduler, retrain_avm, retrain_lstm
    from .features.feature_builder import FeatureBuilder, ALL_FEATURE_COLS

__all__ = [
    "InferenceEngine", "FullPropertyAnalysis", "ValuationResult", "ForecastResult",
    "DealAnalyzer", "DealAnalysis", "PropertyContext",
    "AVMTrainer", "AVMConfig",
    "LSTMTrainer", "LSTMConfig",
    "MLScheduler", "retrain_avm", "retrain_lstm",
    "FeatureBuilder", "ALL_FEATURE_COLS",
]

_LAZY_MAP = {
    "InferenceEngine": ".inference.engine",
    "FullPropertyAnalysis": ".inference.engine",
    "ValuationResult": ".inference.engine",
    "ForecastResult": ".inference.engine",
    "DealAnalyzer": ".inference.deal_analyzer",
    "DealAnalysis": ".inference.deal_analyzer",
    "PropertyContext": ".inference.deal_analyzer",
    "AVMTrainer": ".training.avm_trainer",
    "AVMConfig": ".training.avm_trainer",
    "LSTMTrainer": ".training.lstm_trainer",
    "LSTMConfig": ".training.lstm_trainer",
    "MLScheduler": ".training.scheduler",
    "retrain_avm": ".training.scheduler",
    "retrain_lstm": ".training.scheduler",
    "FeatureBuilder": ".features.feature_builder",
    "ALL_FEATURE_COLS": ".features.feature_builder",
}


def __getattr__(name):
    module_path = _LAZY_MAP.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    module = importlib.import_module(module_path, __name__)
    return getattr(module, name)