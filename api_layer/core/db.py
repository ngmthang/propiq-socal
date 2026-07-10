"""
    PropIQ - Database Session
    Reuses the SQLAlchemy engine/models defined in Layer 1 (date_layer.models.database).

    @author Minh Thang Nguyen
    @version July 9, 2026
"""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from .config import settings

# Layer 1's declarative Base + ORM models (Property, User, etc.)
from data_layer.models.database import Base # noqa: F401

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.POOL_SIZE,
    max_overflow=settings.MAX_OVERFLOW,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency - yields a request-scoped DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()