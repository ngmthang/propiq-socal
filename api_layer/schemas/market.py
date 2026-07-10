from __future__ import annotations

from pydantic import BaseModel, Field

class MarketSnapshot(BaseModel):
    zip_code: str
    median_sale_price: float | None = None
    median_list_price: float | None = None
    median_dom: float | None = Field(None, description="Median day on market")
    inventory_count: int | None = None
    sales_last_90d: int | None = None
    price_per_sqft: float | None = None

class MarketTrendResponse(BaseModel):
    zip_code: str
    snapshot: MarketSnapshot
    forecast_3mo: float
    forecast_6mo: float
    forecast_12mo: float
    trend_signal: str
    model_version: str
    predicted_at: str
    historical_median_price: list[dict] = Field(
        default_factory=list,
        description="Trailing monthly median sale price, e.g. [{'month': '2026-01', 'median_price': 81200}, ...]",
    )