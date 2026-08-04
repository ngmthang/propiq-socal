"""
    PropIQ - Inference Engine
    Single entrypoint for all ML predictions on a property
    Called by the FastAPI layer (Layer 3) for:
        - /api/properties/{id}/valuation
        - /api/properties/{id}/analysis
        - /api/search?include_analysis=true

    @author Minh Thang Nguyen
    @version July 8, 2026
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd
import shap

from typing import Optional
from datetime import datetime
from loguru import logger
from dataclasses import dataclass, field, asdict

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # Type-annotation-only: importing LSTMTrainer at runtime pulls torch
    # (~2GB) via the package __getattr__, defeating the lazy-import design.
    # The real runtime import stays deferred inside load().
    from ..training.lstm_trainer import LSTMTrainer
from ..training.avm_trainer import AVMTrainer
from ..features.feature_builder import FeatureBuilder
from .deal_analyzer import DealAnalyzer, PropertyContext, DealAnalysis
from data_layer.models.database import AVM_REQUIRED_FIELDS

class InsufficientDataError(ValueError):
    """Raised when a property lacks the physical features the AVM needs.
    Valuing such a property would silently run on FeatureBuilder's
    imputation defaults (1500 sqft, 2 baths, ...) - a confident-looking
    number computed from fiction. Callers should surface this as a
    client-side condition (e.g. HTTP 422), not a server error."""

    def __init__(self, missing_fields: list):
        self.missing_fields = list(missing_fields)
        super().__init__(
            'Property lacks required data for a reliable valuation: '
            + ', '.join(self.missing_fields)
        )

@dataclass
class ValuationResult:
    property_id: str
    estimated_value: float
    confidence: float
    price_range_lo: float
    price_range_hi: float
    list_price: Optional[float]
    value_vs_list: Optional[float]
    top_features: list[dict]
    model_version: str
    predicted_at: str

@dataclass
class ForecastResult:
    zip_code: str
    forecast_3mo: float
    forecast_6mo: float
    forecast_12mo: float
    trend_signal:  str # "bullish" | "neutral" | "bearish"
    model_version: str
    predicted_at: str

@dataclass
class FullPropertyAnalysis:
    property_id: str
    valuation: ValuationResult
    forecast: ForecastResult
    deal_score: int
    deal_analysis: Optional[DealAnalysis] = None
    computed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

# FullPropertyAnalysis - add one field
@dataclass
class FullPropertyAnalysis:
    property_id: str
    valuation: ValuationResult
    forecast: ForecastResult
    deal_score: int
    deal_analysis: Optional[DealAnalysis] = None
    recommendations: list[dict] = field(default_factory=list)
    computed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

class InferenceEngine:
    AVM_VERSION = "avm_v1"
    LSTM_VERSION = "lstm_v1"

    def __init__(self, avm: AVMTrainer, lstm: Optional[LSTMTrainer] = None, analyzer: Optional[DealAnalyzer] = None,
                 recommender: Optional["ImprovementRecommender"] = None):
        self.avm = avm
        self.lstm = lstm
        self.analyzer = analyzer
        self.recommender = recommender
        self.builder = FeatureBuilder()

    @classmethod
    def from_paths(cls, avm_path: str, lstm_path: str, enable_ai: bool = True,
                   anthropic_key: Optional[str] = None) -> "InferenceEngine":
        avm = AVMTrainer.load(avm_path)  # required — no valuation without this

        # LSTM is optional: the forecast/analysis endpoints degrade to a
        # neutral placeholder forecast rather than the whole engine (and
        # therefore all valuation endpoints too) failing to load just
        # because forecasting isn't trained yet.
        lstm = None
        serving_only = os.getenv("SERVING_ONLY", "false").lower() in ("1", "true", "yes")
        if serving_only:
            logger.info(
                "SERVING_ONLY set - skipping LSTM/torch load. This replica serves "
                "AVM valuations only; forecasts come from the training worker."
            )
        else:
            try:
                from ..training.lstm_trainer import LSTMTrainer
                lstm = LSTMTrainer.load(lstm_path)
            except Exception as e:
                logger.warning(
                    f"LSTM model not available ({e}). "
                    "Forecast/analysis endpoints will return a neutral placeholder "
                    "forecast until an LSTM model is trained and torch is working."
                )

        analyzer = None
        if enable_ai:
            try:
                analyzer = DealAnalyzer(api_key=anthropic_key)
            except ValueError as e:
                logger.warning(f"{e} Continuing without AI deal analysis.")

        engine = cls(avm, lstm, analyzer)
        from .improvement_recommender import ImprovementRecommender
        engine.recommender = ImprovementRecommender(engine)
        return engine

    @staticmethod
    def _as_dict(property_row) -> dict:
        """
        Normalize either a plain dict (e.g. from ml_layer.utils.db.load_property_for_inference,
        which joins property_features/neighborhoods for full feature fidelity) or a raw
        SQLAlchemy Property ORM instance (e.g. passed directly from a router's DB query,
        which only has Property's own columns — pf/neighborhood features will be absent
        and FeatureBuilder will fall back to its defaults for those) into a plain dict.
        """
        if isinstance(property_row, dict):
            return property_row
        keys = [
            "id", "address", "zip_code", "latitude", "longitude", "property_type",
            "bedrooms", "bathrooms", "building_sqft", "lot_size_sqft", "year_built",
            "list_price", "sale_price", "walk_score", "transit_score", "school_rating",
            "development_score", "adu_eligible", "underbuilt_ratio", "distance_to_cbd_miles",
            "distance_to_coast_miles", "neighborhood_median_price", "price_change_yoy",
            "days_on_market", "inventory_months", "absorption_rate", "neighborhood_name",
        ]
        return {k: v for k, v in ((k, getattr(property_row, k, None)) for k in keys) if v is not None}

    def valuate(self, property_row) -> ValuationResult:
        property_row = self._as_dict(property_row)

        missing = [f for f in AVM_REQUIRED_FIELDS if not property_row.get(f)]
        if missing:
            raise InsufficientDataError(missing)

        df = pd.DataFrame([property_row])
        X, _ = self.builder.build(df)
        pred = self.avm.model.predict(X)[0]
        ci_pct = 0.08
        list_price = property_row.get('list_price')
        return ValuationResult(
            property_id = str(property_row.get('id', 'unknown')),
            estimated_value = round(float(pred), -2),
            confidence = round(max(0.0, 1.0 - ci_pct * 1.5), 2),
            price_range_lo = round(pred * (1 - ci_pct), -2),
            price_range_hi = round(pred * (1 + ci_pct), -2),
            list_price = list_price,
            value_vs_list = round(pred - list_price, 2) if list_price else None,
            top_features = self._shap_drivers(X),
            model_version = self.AVM_VERSION,
            predicted_at = datetime.utcnow().isoformat()
        )

    def _shap_drivers(self, X: pd.DataFrame, top_n: int = 5) -> list[dict]:
        if self.avm.explainer is None: return []
        try:
            X_scaled = self.avm.model.named_steps['scaler'].transform(X)
            shap_vals = self.avm.explainer.shap_values(X_scaled)
            row = shap_vals[0]
            pairs = sorted(zip(X.columns.tolist(), row), key=lambda kx: abs(kx[1]), reverse=True)
            return [{"feature": col, "contribution": round(float(val), 0),
                     "direction": "positive" if val > 0 else "negative"} for col, val in pairs[:top_n]]
        except Exception as e:
            logger.warning(f"SHAP computation failed: {e}")
            return []

    def forecast(self, zip_code: str, market_history_df: Optional[pd.DataFrame] = None) -> ForecastResult:
        if self.lstm is None or market_history_df is None or market_history_df.empty:
            return ForecastResult(
                zip_code=zip_code,
                forecast_3mo=0.0, forecast_6mo=0.0, forecast_12mo=0.0,
                trend_signal="neutral",
                model_version="unavailable",
                predicted_at=datetime.utcnow().isoformat(),
            )

        feat_cols = self.lstm.config.feature_cols
        lb = self.lstm.config.lookback_months
        seq = market_history_df.tail(lb)[feat_cols].values
        if len(seq) < lb:
            pad = np.zeros((lb - len(seq), len(feat_cols)))
            seq = np.vstack([pad, seq])

        pcts = self.lstm.predict(self.lstm.scaler.transform(seq))
        f12 = pcts.get("12mo", 0)
        return ForecastResult(
            zip_code = zip_code,
            forecast_3mo = round(pcts.get("3mo", 0) * 100, 2),
            forecast_6mo = round(pcts.get("6mo", 0) * 100, 2),
            forecast_12mo = round(f12 * 100, 2),
            trend_signal = "bullish" if f12 > 0.05 else "bearish" if f12 < -0.03 else "neutral",
            model_version = self.LSTM_VERSION,
            predicted_at = datetime.utcnow().isoformat(),
        )

    def analyze_property(self, property_row, market_history_df: Optional[pd.DataFrame] = None,
                         comparables: Optional[list[dict]] = None, include_ai: bool = True,
                         include_recommendations: bool = True) -> FullPropertyAnalysis:
        orig = property_row
        property_row = self._as_dict(property_row)
        val = self.valuate(property_row)
        forecast = self.forecast(property_row.get("zip_code", ""), market_history_df)

        deal_analysis = None
        if include_ai and self.analyzer:
            ctx = self._build_context(property_row, val, forecast, comparables or [])
            try:
                deal_analysis = self.analyzer.analyze(ctx)
            except Exception as e:
                logger.warning(f"AI analysis failed, continuing without it: {e}")

        recommendations = []
        # recommender needs the ORM row (prop.features/.neighborhood/.pool/
        # .garage_spaces relationships) - a plain dict (e.g. from
        # ml_layer.utils.db) can't supply those, so it's skipped rather than guessed.
        if include_recommendations and self.recommender and not isinstance(orig, dict):
            try:
                recommendations = [asdict(r) for r in self.recommender.recommend(orig)]
            except Exception as e:
                logger.warning(f"Recommendation engine failed, continuing without it: {e}")

        return FullPropertyAnalysis(
            property_id=str(property_row.get('id', 'unknown')),
            valuation=val,
            forecast=forecast,
            deal_score=deal_analysis.deal_score if deal_analysis else self._heuristic_score(val, forecast,
                                                                                            property_row),
            deal_analysis=deal_analysis,
            recommendations=recommendations,
        )

    def _build_context(self, row: dict, val: ValuationResult,
                       forecast: ForecastResult, comparables: list[dict]) -> PropertyContext:
        return PropertyContext(
            address=row.get("address", "Unknown"), zip_code=row.get("zip_code", ""),
            list_price=row.get("list_price", 0), property_type=row.get("property_type", "SFR"),
            bedrooms=int(row.get("bedrooms", 3)), bathrooms=float(row.get("bathrooms", 2)),
            building_sqft=int(row.get("building_sqft", 1500)), lot_size_sqft=row.get("lot_size_sqft"),
            year_built=row.get("year_built"), avm_value=val.estimated_value,
            avm_confidence=val.confidence, top_value_drivers=val.top_features,
            forecast_3mo=forecast.forecast_3mo, forecast_6mo=forecast.forecast_6mo,
            forecast_12mo=forecast.forecast_12mo,
            development_score=float(row.get("development_score", 50)),
            adu_eligible=bool(row.get("adu_eligible", False)),
            renovation_score=float(row.get("renovation_score", 50)),
            underbuilt_ratio=float(row.get("underbuilt_ratio", 0.3)),
            neighborhood_name=row.get("neighborhood_name"),
            neighborhood_median=float(row.get("neighborhood_median_price", 800000)),
            days_on_market=int(row.get("days_on_market", 30)),
            price_change_yoy=float(row.get("price_change_yoy", 3.0)),
            inventory_months=float(row.get("inventory_months", 2.5)),
            comparables=comparables,
        )

    def _heuristic_score(self, val: ValuationResult, forecast: ForecastResult, row: dict) -> int:
        score = 50
        if val.value_vs_list and val.value_vs_list > 0 and val.list_price:
            score += min(20, val.value_vs_list / val.list_price * 100)
        score += 15 if forecast.forecast_12mo > 5 else -15 if forecast.forecast_12mo > -2 else 0
        if row.get("development_score", 50) > 70: score += 10
        if row.get("adu_eligible"): score += 5
        return int(max(0, min(100, score)))