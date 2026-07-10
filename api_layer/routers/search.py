"""
    PropIQ - Search Router

        GET /api/search?zip=90210&min_price=...&max_price=...&beds=...&include_analysis=true

    @author Minh Thang Nguyen
    @version July 10, 2026
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from data_layer.models.database import Property, PropertyType
from ml_layer.inference.engine import InferenceEngine

from ..core.auth import require_api_key
from ..core.config import settings
from ..core.db import get_db
from ..dependencies.ml import get_inference_engine
from ..schemas.properties import PropertySearchResult, SearchResponse

router = APIRouter(prefix="/api/search", tags=["Search"], dependencies=[Depends(require_api_key)])

@router.get("", response_model=SearchResponse)
def search_properties(
        zip_code: str | None = Query(None, description="Filter by ZIP code"),
        city: str | None = Query(None, description="Filter by city"),
        property_type: str | None = Query(None, description="e.g. single_family, condo"),
        min_price: float | None = Query(None, ge=0),
        max_price: float | None = Query(None, ge=0),
        min_beds: int | None = Query(None, ge=0),
        min_baths: float | None = Query(None, ge=0),
        min_deal_score: int | None = Query(None, ge=0, le=100, description="Only show properties with deal_score >= this"),
        sort_by: str = Query("updated_at", pattern="^(updated_at|list_price|sale_price|deal_score)$"),
        sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
        include_analysis: bool = Query(
            False, description="Run live AVM + deal scoring per result (slower, richer)"
        ),
        page: int = Query(1, ge=1),
        page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
        db: Session = Depends(get_db),
        engine: InferenceEngine = Depends(get_inference_engine),
) -> SearchResponse:
    q = db.query(Property)

    if zip_code:
        q = q.filter(Property.zip_code == zip_code)
    if city:
        q = q.filter(Property.city.ilike(f"%{city}%"))
    if property_type:
        try:
            q = q.filter(Property.property_type == PropertyType(property_type))
        except ValueError:
            pass # unknown type -> ignore filter rather than 500
    if min_price is not None:
        q = q.filter(Property.list_price >= min_price)
    if max_price is not None:
        q = q.filter(Property.list_price <= max_price)
    if min_beds is not None:
        q = q.filter(Property.beds >= min_beds)
    if min_baths is not None:
        q = q.filter(Property.baths >= min_baths)

    sort_col = getattr(Property, sort_by, Property.updated_at)
    q = q.order_by(desc(sort_col) if sort_dir == "desc" else asc(sort_col))

    total = q.count()
    offset = (page - 1) * page_size
    rows = q.offset(offset).limit(page_size).all()

    items: list[PropertySearchResult] = []
    for prop in rows:
        result = PropertySearchResult.model_validate(prop)

        if include_analysis:
            try:
                full = engine.analyze_property(prop, include_ai=False)
                result.estimated_value = full.valuation.estimated_value
                result.deal_score = full.deal_score
            except Exception: # noqa: BLE001
                # Don't let one bad property tank the whole search response.
                pass

        if min_deal_score is not None and (result.deal_score or 0) < min_deal_score:
            continue

        items.append(result)

    return SearchResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_next=offset + len(rows) < total,
    )