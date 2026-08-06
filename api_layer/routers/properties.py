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
from sqlalchemy.orm import Session

from data_layer.models.database import Property
from ml_layer.inference.engine import InferenceEngine, InsufficientDataError
from ml_layer.utils.db import load_market_history

from ..core.auth import get_current_user
from ..core.config import settings
from ..core.db import get_db
from ..dependencies.ml import get_inference_engine
from ..schemas.common import FeatureDriver
from ..schemas.properties import (
    PropertyDetail,
    ValuationResponse,
    ForecastResponse,
    DealAnalysisResponse,
    FullAnalysisResponse,
    RecommendationOut,
)
router = APIRouter(
    prefix="/api/properties",
    tags=["properties"],
    dependencies=[Depends(get_current_user)],
)

def _get_property_or_404(db: Session, property_id: int) -> Property:
    prop = db.query(Property).filter(Property.id == property_id).first()
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )
    return prop

@router.get("/{property_id}", response_model=PropertyDetail)
def get_property(property_id: int, db: Session = Depends(get_db)) -> PropertyDetail:
    prop = _get_property_or_404(db, property_id)

    data = dict(
        id=prop.id, address=prop.address, city=prop.city, zip_code=prop.zip_code,
        beds=prop.bedrooms, baths=prop.bathrooms,
        building_sqft=prop.building_sqft, lot_sqft=prop.lot_size_sqft,
        year_built=prop.year_built, list_price=prop.list_price, sale_price=prop.sale_price,
        latitude=prop.latitude, longitude=prop.longitude,

        property_type=prop.property_type.value if prop.property_type else None,
        county=prop.county, state=prop.state, parcel_number=prop.parcel_number,
        zoning=prop.zoning.value if prop.zoning else None,
        stories=prop.stories, units=prop.units,
        garage_spaces=prop.garage_spaces, pool=prop.pool,

        last_sale_price=prop.last_sale_price, last_sold_date=prop.last_sale_date,
        assessed_value=prop.assessed_value, price_per_sqft=prop.price_per_sqft,

        estimated_value=prop.estimated_value,
        data_source=prop.data_source, updated_at=prop.updated_at,
    )

    if prop.features:
        data.update(
            walk_score=prop.features.walk_score,
            transit_score=prop.features.transit_score,
            bike_score=prop.features.bike_score,
            school_rating=prop.features.school_rating,
            distance_to_downtown_mi=prop.features.distance_to_downtown_mi,
            flood_zone=prop.features.flood_zone,
            fire_hazard_zone=prop.features.fire_hazard_zone,
        )
    if prop.neighborhood:
        data["neighborhood_name"] = prop.neighborhood.neighborhood_name

    return PropertyDetail(**data)

@router.get("/{property_id}/valuation", response_model=ValuationResponse)
def get_valuation(
        property_id: int,
        db: Session = Depends(get_db),
        engine: InferenceEngine = Depends(get_inference_engine),
) -> ValuationResponse:
    prop = _get_property_or_404(db, property_id)

    try:
        result = engine.valuate(prop)
    except InsufficientDataError as exc:
        # Not a server fault: this property (e.g. a county parcel record)
        # simply lacks the physical data a reliable valuation needs.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                'message': 'Insufficient property data for a reliable valuation',
                'missing_fields': exc.missing_fields,
            },
        ) from exc
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
        # Without this, analyze_property() always falls back to the neutral
        # 0/0/0 "unavailable" forecast, regardless of whether an LSTM model
        # is trained - forecast() requires market_history_df to do anything.
        market_history_df = load_market_history(settings.DATABASE_URL, zip_code=prop.zip_code)
        full = engine.analyze_property(prop, market_history_df=market_history_df, include_ai=include_ai)
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
        recommendations=[RecommendationOut(**r) for r in full.recommendations],
        computed_at=full.computed_at,
    )


