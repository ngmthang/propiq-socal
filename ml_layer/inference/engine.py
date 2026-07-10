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

import numpy as np
import pandas as pd
import shap

from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger

from ..training.avm_trainer import AVMTrainer
from ..training.lstm_trainer import LSTMTrainer
from ..features.feature_builder import FeatureBuilder
from .deal_analyzer import DealAnalyzer, PropertyContext, DealAnalysis

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

class InferenceEngine:
    AVM_VERSION = "avm_v1"
    LSTM_VERSION = "lstm_v1"

    def __init__(self, avm: AVMTrainer, lstm: LSTMTrainer, analyzer: Optional[DealAnalyzer] = None):
        self.avm = avm
        self.lstm = lstm
        self.analyzer = analyzer
        self.builder = FeatureBuilder()

    @classmethod
    def from_paths(cls, avm_path: str, lstm_path: str, enable_ai: bool = True,
                   anthropic_key: Optional[str] = None) -> "InferenceEngine":
        avm = AVMTrainer.load(avm_path)
        lstm = LSTMTrainer.load(lstm_path)
        analyzer = DealAnalyzer(api_key=anthropic_key) if enable_ai else None
        return cls(avm, lstm, analyzer)

    def valuate(self, property_row: dict) -> ValuationResult:
        df = pd.DataFrame([property_row])
        X, _ = self.builder.build(df)
        pred = self.avm.model.predict(X)[0]
        ci_pct = 0.08
        list_price = property_row.get('list_price')
        return ValuationResult(
            property_id = str(property_row.get('id', 'unknown')),
            estimated_value = round(pred, -2),
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
            X_scaled = self.avm.model.named_steps['scaled'].transform(X)
            shap_vals = self.avm.explainer.shap_values(X_scaled)
            row = shap_vals[0]
            pairs = sorted(zip(X.columns.tolist(), row), key=lambda kx: abs(kx[1]), reverse=True)
            return [{"feature": col, "impact": round(float(val), 0),
                     "direction": "up" if val > 0 else "down"} for col, val in pairs[:top_n]]
        except Exception as e:
            logger.warning(f"SHAP computation failed: {e}")
            return []

    def forecast(self, zip_code: str, market_history_df: pd.DataFrame) -> ForecastResult:
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

    def analyze_property(self, property_row: dict, market_history_df: pd.DataFrame,
                         comparables: Optional[list[dict]] = None, use_ai: bool = True) -> FullPropertyAnalysis:
        val = self.valuate(property_row)
        forecast = self.forecast(property_row.get("zip_code", ""), market_history_df)

        deal_analysis = None
        if use_ai and self.analyzer:
            ctx = self._build_context(property_row, val, forecast, comparables or [])
            try:
                deal_analysis = self.analyzer.analyze(ctx)
            except Exception as e:
                logger.warning(f"AI analysis failed, continuing without it: {e}")

        return FullPropertyAnalysis(
            property_id = str(property_row.get('id', 'unknown')),
            valuation = val,
            forecast = forecast,
            deal_score = deal_analysis.deal_score if deal_analysis else self._heuristic_score(val, forecast, property_row),
            deal_analysis = deal_analysis,
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