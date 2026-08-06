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

from ..core.auth import get_current_user
from ..core.config import settings
from ..core.db import get_db
from ..dependencies.ml import get_inference_engine
from ..schemas.properties import MapPin, MapPinsResponse, PropertySearchResult, SearchResponse

router = APIRouter(prefix="/api/search", tags=["Search"], dependencies=[Depends(get_current_user)])

# Hard ceiling on pins returned per map request. Past this, the client
# should be zoomed in / bbox narrowed rather than the server dumping more
# points into one response - with 470k+ real parcels in Orange County
# alone, "all of it" is never actually a request we want to serve at once.
MAP_PIN_LIMIT = 4000

def _display_value(prop: Property) -> tuple[float | None, str]:
    """
    Prefer a real market-observed price over the model's estimate, per the
    product goal: real listed/sold price when we have one, predicted value
    otherwise, and an honest "unpriced" rather than a fabricated number
    when we have neither.

    Property.list_price is a real column (Redfin-ingested active listing
    price) as of migration a1c9f3d2e7b4 - it used to be a hybrid alias for
    estimated_value, which is why this used to skip straight to
    last_sale_price. Preference order now: real active listing > real last
    sale > AVM prediction > unpriced.
    """
    if prop.list_price is not None:
        return float(prop.list_price), "listed"
    if prop.last_sale_price is not None:
        return float(prop.last_sale_price), "sold"
    if prop.estimated_value is not None:
        return float(prop.estimated_value), "estimated"
    return None, "unpriced"

@router.get("", response_model=SearchResponse)
def search_properties(
        zip_code: str | None = Query(None, description="Filter by ZIP code"),
        city: str | None = Query(None, description="Filter by city"),
        county: str | None = Query(None, description="Filter by county, e.g. 'Orange'"),
        property_type: str | None = Query(None, description="e.g. single_family, condo"),
        min_price: float | None = Query(None, ge=0),
        max_price: float | None = Query(None, ge=0),
        min_beds: int | None = Query(None, ge=0),
        min_baths: float | None = Query(None, ge=0),
        min_deal_score: int | None = Query(None, ge=0, le=100, description="Only show properties with deal_score >= this"),
        min_lat: float | None = Query(None, description="Optional bbox scoping, syncs list to map viewport"),
        max_lat: float | None = Query(None),
        min_lng: float | None = Query(None),
        max_lng: float | None = Query(None),
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
    if county:
        q = q.filter(Property.county.ilike(f"%{county}%"))
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
    if min_lat is not None and max_lat is not None:
        q = q.filter(Property.latitude.between(min_lat, max_lat))
    if min_lng is not None and max_lng is not None:
        q = q.filter(Property.longitude.between(min_lng, max_lng))

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
                estimated_value = float(full.valuation.estimated_value)
                result.estimated_value = estimated_value
                result.predicted_value = estimated_value
                result.deal_score = int(full.deal_score)
                if prop.list_price:
                    result.value_delta_pct = round(
                        (estimated_value - float(prop.list_price)) / float(prop.list_price) * 100, 2
                    )
            except Exception:  # noqa: BLE001
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

@router.get("/map", response_model=MapPinsResponse)
def search_map_pins(
        min_lat: float = Query(..., description="Current map viewport bounds"),
        max_lat: float = Query(...),
        min_lng: float = Query(...),
        max_lng: float = Query(...),
        zip_code: str | None = Query(None),
        city: str | None = Query(None),
        county: str | None = Query(None, description="e.g. 'Orange' - use to scope to one county"),
        limit: int = Query(MAP_PIN_LIMIT, ge=1, le=MAP_PIN_LIMIT),
        db: Session = Depends(get_db),
) -> MapPinsResponse:
    """
    Points for the map, scoped to whatever's currently in view. Intentionally
    does NOT run live AVM inference per pin - at Orange-County scale that's
    hundreds of thousands of rows, so this only ever reads columns that are
    already populated. Pair with clustering on the frontend (supercluster /
    Mapbox GL's built-in `cluster: true` source) rather than rendering one
    DOM marker per row.
    """
    q = db.query(Property).filter(
        Property.latitude.between(min_lat, max_lat),
        Property.longitude.between(min_lng, max_lng),
    )
    if zip_code:
        q = q.filter(Property.zip_code == zip_code)
    if city:
        q = q.filter(Property.city.ilike(f"%{city}%"))
    if county:
        q = q.filter(Property.county.ilike(f"%{county}%"))

    total_in_bounds = q.count()
    rows = q.limit(limit).all()

    pins: list[MapPin] = []
    for prop in rows:
        value, value_type = _display_value(prop)
        pins.append(
            MapPin(
                id=prop.id,
                latitude=prop.latitude,
                longitude=prop.longitude,
                zip_code=prop.zip_code,
                display_value=value,
                value_type=value_type,
            )
        )

    return MapPinsResponse(
        items=pins,
        total_in_bounds=total_in_bounds,
        truncated=total_in_bounds > len(pins),
    )