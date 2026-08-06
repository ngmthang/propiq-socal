from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field

from .common import FeatureDriver

# Property
class PropertySummary(BaseModel):
    """Lightweight property representation for search/list views."""
    id: int
    address: str
    city: str
    zip_code: str
    beds: int | None = None
    baths: float | None = None
    building_sqft: float | None = None
    lot_sqft: float | None = None
    year_built: int | None = None
    list_price: float | None = None
    sale_price: float | None = None
    latitude: float | None = None
    longitude: float | None = None

    model_config = {"from_attributes": True}

class PropertyDetail(PropertySummary):
    """
    Full property record for the detail page - Property's own columns plus
    PropertyFeature (walk/transit/school scores, hazard zones) and
    Neighborhood context, which don't live directly on Property so the
    router builds this from a merged dict rather than a plain
    model_validate(prop).
    """
    property_type: str | None = None
    county: str | None = None
    state: str | None = None
    parcel_number: str | None = None
    zoning: str | None = None
    stories: int | None = None
    units: int | None = None
    garage_spaces: int | None = None
    pool: bool | None = None

    last_sale_price: float | None = None
    last_sold_date: datetime | None = None
    assessed_value: float | None = None
    price_per_sqft: float | None = None
    hoa_fee: float | None = None
    days_on_market: int | None = None

    walk_score: int | None = None
    transit_score: int | None = None
    bike_score: int | None = None
    school_rating: float | None = None
    distance_to_downtown_mi: float | None = None
    flood_zone: str | None = None
    fire_hazard_zone: str | None = None
    neighborhood_name: str | None = None

    estimated_value: float | None = None
    deal_score: int | None = None
    data_source: str | None = None
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
    recommendations: list[RecommendationOut] = []
    computed_at: str

# Search
class PropertySearchResult(PropertySummary):
    deal_score: int | None = None
    estimated_value: float | None = None
    deal_analysis_summary: str | None = None
    predicted_value: float | None = None
    value_delta_pct: float | None = None

class SearchResponse(BaseModel):
    items: list[PropertySearchResult]
    total: int
    page: int
    page_size: int
    has_next: bool

class MapPin(BaseModel):
    """
    Lightweight point for the map view. Deliberately excludes every field
    that requires a live AVM/SHAP call - with 470k+ real parcels in one
    county, running inference per pin isn't viable, so pins only ever
    carry data that's already sitting in a column.
    """
    id: int
    latitude: float
    longitude: float
    zip_code: str
    display_value: float | None = None
    value_type: str = Field(
        ...,
        description="'listed' (real active listing) | 'sold' (real last-sale price) | "
                    "'estimated' (AVM prediction) | 'unpriced'",
    )

    model_config = {"from_attributes": True}

class MapPinsResponse(BaseModel):
    items: list[MapPin]
    total_in_bounds: int
    truncated: bool = Field(
        ..., description="True if total_in_bounds exceeds what was returned - zoom in for the rest"
    )

class RecommendationOut(BaseModel):
    type: str
    title: str
    rationale: str
    feasible: bool
    feasibility_reason: str
    est_cost: float | None = None
    value_lift_pct: float | None = None
    confidence: float = 0.0
    method: str | None = None
    caveat: str | None = None