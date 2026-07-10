"""
    PropIQ - Market Router

        GET /api/market/{zip} - current snapshot + LSTM 3/6/12mo forecast for a ZIP

    @author Minh Thang Nguyen
    @version July 10, 2026
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from data_layer.models.database import Property
from ml_layer.inference.engine import InferenceEngine

from ..core.auth import require_api_key
from ..core.db import get_db
from ..dependencies.ml import get_inference_engine
from ..schemas.market import MarketSnapshot, MarketTrendResponse

router = APIRouter(prefix="/api/market", tags=["market"], dependencies=[Depends(require_api_key)])

def _build_snapshot(db: Session, zip_code: str) -> MarketSnapshot:
    row = (
        db.query(
            func.percentile_cont(0.5).within_group(Property.sale_price.asc()).label("median_sale"),
            func.percentile_cont(0.5).within_group(Property.list_price.asc()).label("median_list"),
            func.percentile_cont(0.5).within_group(Property.days_on_market.asc()).label("median_dom"),
            func.count(Property.id).label("inventory_count"),
        )
        .filter(Property.zip_code == zip_code)
        .first()
    )

    return MarketSnapshot(
        zip_code=zip_code,
        median_sale_price=float(row.median_sale) if row and row.median_sale else None,
        median_list_price=float(row.median_list) if row and row.median_list else None,
        median_dom=float(row.median_dom) if row and row.median_dom else None,
        inventory_count=int(row.inventory_count) if row else 0,
        sales_last_90d=None, # left for a dedicated data_layer aggregate query
        price_per_sqft=None, # left for a dedicated data_layer aggregate query
    )

@router.get("/{zip_code}", response_model=MarketTrendResponse)
def get_market_trend(
        zip_code: str,
        db: Session = Depends(get_db),
        engine: InferenceEngine = Depends(get_inference_engine),
) -> MarketTrendResponse:
    exists = db.query(Property.id).filter(Property.zip_code == zip_code).first()
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No property data on file for ZIP '{zip_code}'",
        )

    try:
        forecast = engine.forecast_zip(zip_code)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Forecast failed: {e}",
        ) from e

    snapshot = _build_snapshot(db, zip_code)

    return MarketTrendResponse(
        zip_code=zip_code,
        snapshot=snapshot,
        forecast_3mo=forecast.forecast_3mo,
        forecast_6mo=forecast.forecast_6mo,
        forecast_12mo=forecast.forecast_12mo,
        trend_signal=forecast.trend_signal,
        model_version=forecast.model_version,
        predicted_at=forecast.predicted_at,
        historical_median_price=[], # populate from a data_layer time-series query
    )