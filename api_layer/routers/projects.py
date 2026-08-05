"""
    PropIQ — Projects/Tasks API (Kanban board)

    Note on naming: the frontend calls this "projectsApi" and the board shows
    "Projects", but what's actually listed/dragged are Task rows (a Project
    has many Tasks; the board operates at the Task level). Kept the URL
    prefix as /api/projects to match the existing frontend contract in
    frontend/src/api/client.js rather than touching working frontend code.

    @author Minh Thang Nguyen
    @version July 24, 2026
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from data_layer.models.database import Task, Project, TaskStatus, Property, User, ProjectStatus
from ..schemas.projects import TaskOut, TaskStatusUpdate, TaskCreateFromRecommendation, FRONTEND_STATUSES

from ..core.auth import get_current_user
from ..core.db import get_db


router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user)]
)

# Frontend column id <-> DB enum value. DB uses 'todo'/'in-progress' (hyphen);
# frontend uses 'backlog'/'in_progress' (underscore) — different vocabulary
# on each side, translated here rather than changing either.
_DB_TO_FRONTEND = {
    TaskStatus.TODO: "backlog",
    TaskStatus.IN_PROGRESS: "in_progress",
    TaskStatus.REVIEW: "review",
    TaskStatus.DONE: "done",
}
_FRONTEND_TO_DB = {v: k for k, v in _DB_TO_FRONTEND.items()}


def _to_task_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        title=task.title,
        status=_DB_TO_FRONTEND.get(task.status, "backlog"),
        property_address=task.project.property.address if task.project and task.project.property else None,
        assignee=task.assignee.full_name if task.assignee else None,
    )


@router.get("", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db)) -> list[TaskOut]:
    tasks = (
        db.query(Task)
        .options(
            joinedload(Task.project).joinedload(Project.property),
            joinedload(Task.assignee),
        )
        .order_by(
            Task.priority.desc(),
            Task.due_date.asc().nullslast()
        )
        .all()
    )
    return [_to_task_out(t) for t in tasks]


@router.patch("/{task_id}", response_model=TaskOut)
def update_task_status(
        task_id: int,
        payload: TaskStatusUpdate,
        db: Session = Depends(get_db)
) -> TaskOut:
    if payload.status not in FRONTEND_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of {sorted(FRONTEND_STATUSES)}",
        )

    task = (
        db.query(Task)
        .options(
            joinedload(Task.project).joinedload(Project.property),
            joinedload(Task.assignee)
        )
        .filter(Task.id == task_id)
        .first()
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}"
        )

    task.status = _FRONTEND_TO_DB[payload.status]
    db.commit()
    db.refresh(task)
    return _to_task_out(task)

@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task_from_recommendation(
        payload: TaskCreateFromRecommendation,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
) -> TaskOut:
    """
    'Add to project' from a RecommendationCard: find-or-create a Project
    for this property, then add a Task under it representing the
    recommended improvement. Kept as a single POST /api/projects (not a
    separate /from-recommendation path) since that's the only creation
    flow this API has right now - matches the router's existing habit of
    keeping the frontend contract at one predictable prefix.
    """
    prop = db.query(Property).filter(Property.id == payload.property_id).first()
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property not found: {payload.property_id}",
        )

    project = db.query(Project).filter(Project.property_id == payload.property_id).first()
    if project is None:
        project = Project(
            property_id=payload.property_id,
            manager_id=current_user.id,
            title=f"{prop.address} improvements",
            description="Auto-created from a recommended improvement.",
            status=ProjectStatus.PLANNING,
            project_type="improvement",
            estimated_value_add=payload.value_lift_pct,
        )
        db.add(project)
        db.flush()  # get project.id without a separate round trip

    desc_parts = [payload.rationale]
    if payload.est_cost is not None:
        desc_parts.append(f"Est. cost: ${payload.est_cost:,.0f}")
    if payload.value_lift_pct is not None:
        desc_parts.append(f"Est. value lift: {payload.value_lift_pct:.1f}%")
    if payload.method == "rule_of_thumb":
        desc_parts.append("(Rule-of-thumb estimate, not AVM-modeled)")

    task = Task(
        project_id=project.id,
        title=payload.title,
        description=" · ".join(desc_parts),
        status=TaskStatus.TODO,
        priority=2,
        tags=[payload.rec_type],
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    # Reload with the same eager-loading the GET endpoint uses, so
    # _to_task_out's task.project.property access doesn't trigger a
    # lazy-load outside the session.
    task = (
        db.query(Task)
        .options(joinedload(Task.project).joinedload(Project.property), joinedload(Task.assignee))
        .filter(Task.id == task.id)
        .first()
    )
    return _to_task_out(task)