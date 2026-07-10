"""
    PropIQ - Properties Router

        GET /api/properties/{id} - property detail
        GET /api/properties/{id}/valuations - AVM valuation (XGBoost + SHAP)
        GET /api/properties/{id}/analysis - full analysis (valuation + forecast + deal score + AI narrative)

    @author Minh Thang Nguyen
    @version July 9, 2026
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from jedi.third_party.typeshed.stubs.docutils.docutils.utils.math.mathml_elements import mo
from sqlalchemy.orm import Session

from data_layer.models.database import Property
from ml_layer.inference.engine import InferenceEngine

from ..core.auth import require_api_key
from ..core.db import get_db
from ..dependencies.ml import get_inference_engine
from ..schemas.common import FeatureDriver
from ..schemas.properties import (
    PropertyDetail,
    ValuationResponse,
    ForecastResponse,
    DealAnalysisResponse,
    FullAnalysisResponse
)

router = APIRouter(
    prefix="/api/properties",
    tags=["properties"],
    dependencies=[Depends(require_api_key)],
)

def _get_property_or_404(db: Session, property_id: str) -> Property:
    prop = db.query(Property).filter(Property.id == property_id).first()
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )
    return prop

@router.get("/{property_id}", response_model=PropertyDetail)
def get_property(property_id: str, db: Session = Depends(get_db)) -> PropertyDetail:
    prop = _get_property_or_404(db, property_id)
    return PropertyDetail.model_validate(prop)

@router.get("/{property_id}/valuation", response_model=ValuationResponse)
def get_valuation(
        property_id: str,
        db: Session = Depends(get_db),
        engine: InferenceEngine = Depends(get_inference_engine),
) -> ValuationResponse:
    prop = _get_property_or_404(db, property_id)

    try:
        result = engine.estimate_value(prop) if hasattr(engine, "estimate_value") \
            else engine.avm.predict_one(prop, feature_builder=engine.builder)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Valuation failed: {exc}",
        ) from exc

    return ValuationResponse(
        property_id=result.property_id,
        estimated_value=result.estimated_value,
        confidence=result.confidence,
        price_range_lo=result.price_range_lo,
        price_range_hi=result.price_range_hi,
        list_price=result.list_price,
        value_vs_list=result.value_vs_list,
        top_features=[FeatureDriver(**f) for f in result.top_features],
        model_version=result.model_version,
        predicted_at=result.predicted_at,
    )

@router.get("/{property_id}/analysis", response_model=FullAnalysisResponse)
def get_analysis(
        property_id: str,
        include_ai: bool = True,
        db: Session = Depends(get_db),
        engine: InferenceEngine = Depends(get_inference_engine),
) -> FullAnalysisResponse:
    """
    Full property analysis: AVM valuation, LSTM ZIP forecast, deal score,
    and (Optionally) a Claude-generated investment narrative.
    """
    prop = _get_property_or_404(db, property_id)

    try:
        full = engine.analyze_property(prop, include_ai=include_ai)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {exc}",
        ) from exc

    valuation = ValuationResponse(
        property_id=full.valuation.property_id,
        estimated_value=full.valuation.estimated_value,
        confidence=full.valuation.confidence,
        price_range_lo=full.valuation.price_range_lo,
        price_range_hi=full.valuation.price_range_hi,
        list_price=full.valuation.list_price,
        value_vs_list=full.valuation.value_vs_list,
        top_features=[FeatureDriver(**f) for f in full.valuation.top_features],
        model_version=full.valuation.model_version,
        predicted_at=full.valuation.predicted_at,
    )

    forecast = ForecastResponse(
        zip_code=full.forecast.zip_code,
        forecast_3mo=full.forecast.forecast_3mo,
        forecast_6mo=full.forecast.forecast_6mo,
        forecast_12mo=full.forecast.forecast_12mo,
        trend_signal=full.forecast.trend_signal,
        model_version=full.forecast.model_version,
        predicted_at=full.forecast.predicted_at,
    )

    deal_analysis = None
    if full.deal_analysis is not None:
        deal_analysis = DealAnalysisResponse(
            summary=full.deal_analysis.summary,
            strengths=full.deal_analysis.strengths,
            risks=full.deal_analysis.risks,
            recommended_action=full.deal_analysis.recommended_actions,
            investor_fit=getattr(full.deal_analysis, "investor_fit", None),
        )

    return FullAnalysisResponse(
        property_id=full.property_id,
        valuation=valuation,
        forecast=forecast,
        deal_score=full.deal_score,
        deal_analysis=deal_analysis,
        computed_at=full.computed_at,
    )


