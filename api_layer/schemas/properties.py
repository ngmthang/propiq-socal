from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field

from .common import FeatureDriver

# Property
class PropertySummary(BaseModel):
    """Lightweight property representation for search/list views."""
    id: str
    address: str
    city: str
    zip_code: str
    beds: int | None = None
    baths: float | None = None
    building_sqft: int | None = None
    lot_sqft: int | None = None
    year_built: int | None = None
    list_price: float | None = None
    sale_price: float | None = None
    latitude: float | None = None
    longitude: float | None = None

    model_config = {"from_attributes": True}

class PropertyDetail(PropertySummary):
    """Fully property record, joined with the lastest cached valuation if present."""
    zoning: str | None = None
    hoa_fee: float | None = None
    days_on_market: int | None = None
    last_sold_date: datetime | None = None
    estimated_value: float | None = None
    deal_score: int | None = None
    updated_at: datetime | None = None

# Valuation
class ValuationResponse(BaseModel):
    property_id: str
    estimated_value: float
    confidence: float = Field(..., ge=0, le=1)
    price_range_lo: float
    price_range_hi: float
    list_price: float | None = None
    value_vs_list: float | None = Field(
        None, description="estimated_value - list_price, positive means undervalued"
    )
    top_features: list[FeatureDriver]
    model_version: str
    predicted_at: str

# Forecast (nested inside full analysis)
class ForecastResponse(BaseModel):
    zip_code: str
    forecast_3mo: float = Field(..., description="% price change, 3 months out")
    forecast_6mo: float
    forecast_12mo: float
    trend_signal: str = Field(..., description="'bullish' | 'neutral' | 'bearish'")
    model_version: str
    predicted_at: str

# Deal analysis (Claude narrative)
class DealAnalysisResponse(BaseModel):
    summary: str
    strengths: list[str]
    risks: list[str]
    recommended_action: str
    investor_fit: str | None = None

# Full analysis (composite endpoint)
class FullAnalysisResponse(BaseModel):
    property_id: str
    valuation: ValuationResponse
    forecast: ForecastResponse
    deal_score: int = Field(..., ge=0, le=100)
    deal_analysis: DealAnalysisResponse | None = None
    computed_at: str

# Search
class PropertySearchResult(PropertySummary):
    deal_score: int | None = None
    estimated_value: float | None = None
    deal_analysis_summary: str | None = None

class SearchResponse(BaseModel):
    items: list[PropertySearchResult]
    total: int
    page: int
    page_size: int
    has_next: bool