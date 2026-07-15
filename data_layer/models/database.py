"""
    PropIQ - Database Models
    SQLAlchemy ORM models for all core entities

    @author Minh Thang Nguyen
    @version: June 19, 2026
"""

from datetime import datetime
from sqlalchemy import(
    create_engine, Column, Integer, Float, String, Text,
    Boolean, DateTime, ForeignKey, JSON, Enum, Index
)
from sqlalchemy.orm import declarative_base, relationship, Session
from sqlalchemy.dialects.postgresql import ARRAY
import enum

Base = declarative_base()

# Enums Declaration

class PropertyType(enum.Enum):
    SINGLE_FAMILY = 'single_family'
    MULTI_FAMILY = 'multi_family'
    CONDO = 'condo'
    TOWNHOUSE = 'townhouse'
    COMMERCIAL = 'commercial'
    MIXED_USE = 'mixed_use'
    VACANT_LAND = 'vacant_land'
    INDUSTRIAL = 'industrial'

class ZoningType(enum.Enum):
    RESIDENTIAL_LOW = 'R1'
    RESIDENTIAL_MEDIUM = 'R2'
    RESIDENTIAL_HIGH = 'R3'
    COMMERCIAL = 'C1'
    COMMERCIAL_GENERAL = 'C2'
    INDUSTRIAL_LIGHT = 'I1'
    INDUSTRIAL_HEAVY = 'I2'
    MIXED_USE = 'M1'
    AGRICULTURAL = 'A1'

class ProjectStatus(enum.Enum):
    DRAFT = 'draft'
    PLANNING = 'planning'
    IN_PROGRESS = 'in-progress'
    ON_HOLD = 'on-hold'
    COMPLETED = 'completed'
    CANCELED = 'canceled'

class TaskStatus(enum.Enum):
    TODO = 'todo'
    IN_PROGRESS = 'in-progress'
    REVIEW = 'review'
    DONE = 'done'

class UserRole(enum.Enum):
    ADMIN = 'admin'
    MANAGER = 'manager'
    ANALYST = 'analyst'
    CLIENT = 'client'

# User & Auth
class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.CLIENT, nullable=False)
    is_active = Column(Boolean, default=True)
    avatar_url = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    properties = relationship('Property', back_populates='owner')
    projects = relationship('Project', back_populates='manager', foreign_keys='Project.manager_id')
    tasks = relationship('Task', back_populates='assignee', foreign_keys='Task.assignee_id')

    def __repr__(self):
        return f'<User {self.full_name} [{self.role.value}]>'

# Property (core entity)
class Property(Base):
    """
    Core Property record. Populated from scrapers + user input.
    All ML predictions are sorted in PropertyValuation(separate table)
    """
    __tablename__ = 'properties'

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    # Location
    address = Column(String(512), nullable=False)
    city = Column(String(100), nullable=False)
    state = Column(String(2), default='CA', nullable=False)
    zip_code = Column(String(5), nullable=False)
    county = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    parcel_number = Column(String(50), unique=True)

    # Physical Attributes
    property_type = Column(Enum(PropertyType), nullable=False)
    zoning = Column(Enum(ZoningType), nullable=False)
    lot_size_sqft = Column(Float)
    building_sqft = Column(Float)
    year_built = Column(Integer)
    bedrooms = Column(Integer)
    bathrooms = Column(Integer)
    stories = Column(Integer)
    units = Column(Integer, default=1)
    garage_spaces = Column(Integer)
    pool = Column(Boolean, default=False)

    # Valuation (current market)
    last_sale_price = Column(Float)
    last_sale_date = Column(DateTime)
    assessed_value = Column(Float)
    estimated_value = Column(Float)
    price_per_sqft = Column(Float)

    # Metadata
    data_source = Column(String(50))
    source_url = Column(String(1024))
    raw_data = Column(JSON)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    owner = relationship('User', back_populates='properties')
    valuations = relationship('PropertyValuation', back_populates='property', cascade='all, delete-orphan')
    projects = relationship('Project', back_populates='property')
    features = relationship('PropertyFeature', back_populates='property', uselist=False)
    neighborhood = relationship('Neighborhood', back_populates='properties',
                                primaryjoin='Property.zip_code == foreign(Neighborhood.zip_code)')
    price_history = relationship('PriceHistory', back_populates='property', cascade='all, delete-orphan')

    __table_args__ = (
        Index('ix_prop_location', 'latitude', 'longitude'),
        Index('ix_prop_zip', 'zip_code'),
        Index('ix_prop_city', 'city'),
    )

    def __repr__(self):
        return f'<Property {self.address} [{self.city}]>'

class PropertyFeature(Base):
    """
    Engineered features used as ML model inputs.
    Computed from raw Property data + neighborhood data.
    """
    __tablename__ = 'property_features'

    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey('properties.id'), unique=True)

    # Derive Metrics
    lot_to_building_ratio = Column(Float)
    age_years = Column(Integer)
    price_per_sqft = Column(Float)
    walk_score = Column(Integer)
    transit_score = Column(Integer)
    bike_score = Column(Integer)

    # Neighborhood Context
    median_income = Column(Float)
    crime_index = Column(Float)
    school_rating = Column(Float)
    distance_to_downtown_mi = Column(Float)
    distance_to_transit_mi = Column(Float)
    flood_zone = Column(String(10))
    fire_hazard_zone = Column(String(20))

    # Zoning Potential
    max_allowed_units = Column(Integer)
    far_ratio = Column(Float) # Floor-Area Ratio
    setback_front_ft = Column(Float)
    setback_rear_ft = Column(Float)
    height_limit_ft = Column(Float)

    # ADU / Development Potential
    adu_eligible = Column(Boolean)
    adu_max_sqft = Column(Integer)
    development_score = Column(Float) # 0-100, ML computed

    computed_at = Column(DateTime, default=datetime.utcnow)
    property = relationship('Property', back_populates='features')

class PriceHistory(Base):
    """Historical price events: sales, listings, assessments."""
    __tablename__ = 'price_history'

    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey('properties.id'), nullable=False)
    event_type = Column(String(20)) # 'sale', 'listing', 'assessment', 'estimate'
    price = Column(Float, nullable=False)
    price_sqft = Column(Float)
    date = Column(DateTime, nullable=False)
    source = Column(String(50))
    notes = Column(Text)

    property = relationship('Property', back_populates='price_history')

    __table_args__ = (
        Index('ix_price_history_prop_date', 'property_id', 'date'),
    )

# ML Valuations & Predictions
class PropertyValuation(Base):
    """
    Every ML model run that produces a valuation or recommendation.
    Versioned so we can track model improvement over time.
    """
    __tablename__ = 'property_valuations'

    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey('properties.id'), nullable=False)

    # Model Info
    model_name = Column(String(50))
    model_version = Column(String(20))
    model_type = Column(String(30))

    # Core Prediction
    predicted_value = Column(Float)
    predicted_score = Column(Float) # 0-1
    value_lower_bound = Column(Float) # 80% confidence interval
    value_upper_bound = Column(Float)

    # Improvement Recommendations
    recommended_additions = Column(JSON) # [{'type': 'ADU','value_add':8500},...]
    development_potential = Column(JSON) # Zoning upgrade scenarios
    roi_projections = Column(JSON) # 1yr, 3yr, 5yr estimates

    # Explainability (SHAP values)
    feature_importances = Column(JSON) # {feature: shap_value, ...}
    top_value_drives = Column(JSON) # Top 5 features driving this prediction

    predicted_at = Column(DateTime, default=datetime.utcnow)
    property = relationship('Property', back_populates='valuations')

    __table_args__ = (
        Index('ix_valuation_prop_model', 'property_id', 'model_name'),
    )

# Neighborhood / Market Data
class Neighborhood(Base):
    """
    Aggregated neighborhood stats keyed by zip code.
    Updated weekly by the pipeline.
    """
    __tablename__ = 'neighborhoods'

    id = Column(Integer, primary_key=True)
    zip_code = Column(String(5), nullable=False, index=True)
    city = Column(String(100))
    county = Column(String(100))
    neighborhood_name = Column(String(200))

    # Market Data
    median_home_price = Column(Float)
    median_price_sqft = Column(Float)
    avg_days_on_market = Column(Integer)
    inventory_count = Column(Integer)
    months_of_supply = Column(Float)
    price_change_yoy = Column(Float) # year-over-year %
    price_change_mom = Column(Float) # month-over-month %

    # Demographics
    population = Column(Integer)
    median_income = Column(Float)
    median_age = Column(Float)
    owner_occupied_pct = Column(Float)
    renter_occupied_pct = Column(Float)

    # Amenities Scores
    avg_school_rating = Column(Float)
    walk_score = Column(Integer)
    transit_score = Column(Integer)
    restaurant_score = Column(Integer)
    park_count = Column(Integer)

    # Development Trends
    new_permits_ytd = Column(Integer)
    adu_permits_ytd = Column(Integer)
    commercial_vacancy = Column(Float)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    properties = relationship('Property', back_populates='neighborhoods',
                              primaryjoin='Neighborhood.zip_code == foreign(Property.zip_code)',
                              foreign_keys='Property.zip_code')

class MarketTrend(Base):
    """Time-series market snapshots - feeds the LSTM forecasting model."""
    __tablename__ = 'market_trends'

    id = Column(Integer, primary_key=True)
    zip_code = Column(String(5), nullable=False, index=True)
    snapshot_date = Column(DateTime, nullable=False)

    median_price = Column(Float)
    active_listings = Column(Integer)
    closed_sales = Column(Integer)
    avg_dom = Column(Integer)
    list_to_sale = Column(Float) # list price / sale price ratio
    absorption_rate = Column(Float)

    __table_args__ = (
        Index('ix_trend_zip_date', 'zip_code', 'snapshot_date'),
    )

# Project Management
class Project(Base):
    """
    A development or renovation project tied to a property.
    The PM dashboard tracks these.
    """
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey('properties.id'))
    manager_id = Column(Integer, ForeignKey('users.id'))
    client_id = Column(Integer, ForeignKey('users.id'))

    title = Column(String(255), nullable=False)
    description = Column(Text)
    status = Column(Enum(ProjectStatus), default=ProjectStatus.PLANNING)
    project_type = Column(String(100)) # 'ADU build', 'renovation', ...

    budget = Column(Float)
    spent = Column(Float, default=0.0)
    estimated_value_add = Column(Float)

    start_date = Column(DateTime)
    target_end_date = Column(DateTime)
    actual_end_date = Column(DateTime)

    progress_pct = Column(Float, default=0.0)
    notes = Column(Text)
    attachments = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    property = relationship('Property', back_populates='projects')
    manager = relationship('User', back_populates='projects', foreign_keys=[manager_id])
    client = relationship('User', back_populates='projects', foreign_keys=[client_id])
    tasks = relationship('Task', back_populates='project', cascade='all, delete-orphan')
    milestones = relationship('Milestone', back_populates='project', cascade='all, delete-orphan')

class Task(Base):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    assignee_id = Column(Integer, ForeignKey('users.id'))
    parent_id = Column(Integer, ForeignKey('tasks.id'))

    title = Column(String(255), nullable=False)
    description = Column(Text)
    status = Column(Enum(TaskStatus), default=TaskStatus.TODO)
    priority = Column(Integer, default=2) # 1=low, 2=med, 3=high, 4=urgent
    due_date = Column(DateTime)
    estimated_hrs = Column(Float)
    tags = Column(JSON)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship('Project', back_populates='tasks')
    assignee = relationship('User', back_populates='tasks',
                            foreign_keys=[assignee_id])
    subtasks = relationship('Task', back_populates='parent')
    parent = relationship('Task', back_populates='subtasks', remote_side=[id])

class Milestone(Base):
    __tablename__ = 'milestones'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    due_date = Column(DateTime)
    completed_at = Column(DateTime)
    is_completed = Column(Boolean, default=False)

    project = relationship('Project', back_populates='milestones')

# Data Pipeline Tracking
class ScrapeJob(Base):
    """Tracks every scraper run for observability + debugging."""
    __tablename__ = 'scrape_jobs'

    id = Column(Integer, primary_key=True)
    source = Column(String(50)) # 'zillow', 'redfin', 'county_assessor'
    job_type = Column(String(50)) # 'full_async', 'incremental', 'single'
    status = Column(String(20)) # 'running', 'success', 'failed', 'partial'

    records_fetched = Column(Integer, default=0)
    records_saved = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    records_skipped = Column(Integer, default=0)

    start_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    duration_secs = Column(Float)
    error_log = Column(Text)
    job_metadata = Column("metadata", JSON)

# DB Factory
def get_engine(database_url: str):
    return create_engine(database_url,
                         pool_size=10,
                         max_overflow=20,
                         pool_pre_ping=True,
                         echo=False,)

def create_tables(engine):
    Base.metadata.create_all(engine)

def get_session(engine) -> Session:
    return Session(engine)


