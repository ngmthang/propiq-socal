"""
    PropIQ - Admin Router
    Operational visibility for the AdminDashboard: scraper/pipeline job
    history from scrape_jobs, and on-disk model artifact status.

    Admin-only: every route requires an authenticated user with
    role == ADMIN (require_admin). Note the ingest scripts' system bot
    accounts are deliberately role=CLIENT + is_active=False so they can
    never satisfy this check.

    @author Minh Thang Nguyen
    @version August 2, 2026
"""

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from data_layer.models.database import Property, ScrapeJob
from ..core.auth import require_admin
from ..core.config import settings
from ..core.db import get_db

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get("/pipeline/status")
def pipeline_status(db: Session = Depends(get_db)) -> dict:
    """Recent scraper runs plus per-source rollups for the dashboard."""
    recent_jobs = (
        db.execute(select(ScrapeJob).order_by(ScrapeJob.id.desc()).limit(20))
        .scalars()
        .all()
    )

    # Latest job per source (window-free version: small table, keep it simple)
    latest_per_source: dict[str, ScrapeJob] = {}
    for job in recent_jobs:
        if job.source and job.source not in latest_per_source:
            latest_per_source[job.source] = job

    property_counts = dict(
        db.execute(
            select(Property.data_source, func.count(Property.id))
            .group_by(Property.data_source)
        ).all()
    )

    def job_out(j: ScrapeJob) -> dict:
        return {
            "id": j.id,
            "source": j.source,
            "job_type": j.job_type,
            "status": j.status,
            "records_fetched": j.records_fetched,
            "records_saved": j.records_saved,
            "records_updated": j.records_updated,
            "records_skipped": j.records_skipped,
            "started_at": j.start_at.isoformat() if j.start_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            "duration_secs": j.duration_secs,
            "error_log": j.error_log,
        }

    return {
        "recent_jobs": [job_out(j) for j in recent_jobs],
        "sources": {
            source: {
                "last_run": job_out(job),
                "property_count": property_counts.get(source, 0),
            }
            for source, job in latest_per_source.items()
        },
        "total_properties": int(sum(property_counts.values())),
        "generated_at": datetime.utcnow().isoformat(),
    }


def _model_info(name: str, path_str: str) -> dict:
    """Describe one model's on-disk artifacts: present, metrics, freshness.
    Reads the same metrics.json the trainers write via save()."""
    p = Path(path_str)
    info: dict = {"name": name, "path": path_str, "artifacts_present": False,
                  "metrics": None, "last_trained_at": None}

    if not p.exists():
        return info

    files = [f for f in p.iterdir() if f.is_file()]
    info["artifacts_present"] = bool(files)
    if files:
        newest = max(f.stat().st_mtime for f in files)
        info["last_trained_at"] = datetime.utcfromtimestamp(newest).isoformat()

    metrics_file = p / "metrics.json"
    if metrics_file.exists():
        try:
            info["metrics"] = json.loads(metrics_file.read_text())
        except (json.JSONDecodeError, OSError):
            info["metrics"] = {"error": "metrics.json unreadable"}

    return info


@router.get("/models")
def model_registry() -> dict:
    """Status of the trained model artifacts the API serves from."""
    return {
        "models": [
            _model_info("avm_xgboost", settings.AVM_MODEL_PATH),
            _model_info("lstm_forecaster", settings.LSTM_MODEL_PATH),
        ],
        "generated_at": datetime.utcnow().isoformat(),
    }