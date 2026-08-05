from __future__ import annotations

from pydantic import BaseModel

# Frontend Kanban columns use different vocabulary than the DB's TaskStatus
# enum (backlog/in_progress vs todo/in-progress) — translated at the API
# boundary in routers/projects.py rather than changing either side.
FRONTEND_STATUSES = {"backlog", "in_progress", "review", "done"}


class TaskOut(BaseModel):
    id: int
    title: str
    status: str  # frontend vocabulary: backlog | in_progress | review | done
    property_address: str | None = None
    assignee: str | None = None

    model_config = {"from_attributes": True}


class TaskStatusUpdate(BaseModel):
    status: str

class TaskCreateFromRecommendation(BaseModel):
    property_id: int
    rec_type: str
    title: str
    rationale: str
    est_cost: float | None = None
    value_lift_pct: float | None = None
    method: str | None = None