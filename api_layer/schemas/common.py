from __future__ import annotations

from typing import Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

class FeatureDriver(BaseModel):
    """One SHAP-derived feature contribution shown to the user."""
    feature: str
    contribution: float = Field(..., description="Signed $ or % impact on valuation")
    direction: str = Field(..., description="'positive' or 'negative'")
    description: str | None = None

class ErrorResponse(BaseModel):
    detail: str

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool