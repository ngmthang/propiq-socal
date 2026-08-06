"""
    PropIQ — Seed Data Generator
    Generates realistic synthetic SoCal property data for development/testing,
    so the AVM can be trained and the frontend has something real to render
    before live scrapers are wired up with API keys.

    Run:
        python -m data_layer.seeds.seed_data

    @author Minh Thang Nguyen
    @version July 15, 2026
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from alembic.config import Config
from alembic import command

from data_layer.models.database import (
    get_engine, get_session,
    User, Property, PropertyFeature, PriceHistory, PropertyValuation,
    Neighborhood, MarketTrend, Project, Task, Milestone,
    PropertyType, ZoningType, ProjectStatus, TaskStatus, UserRole,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://propiq:propiq@localhost:5433/propiq",  # local Docker Postgres default
)

# zip_code -> (city, county, lat, lng, base $/sqft, neighborhood name)
AREAS = {
    "90210": ("Beverly Hills", "Los Angeles", 34.0901, -118.4065, 1450, "Beverly Hills"),
    "90265": ("Malibu",        "Los Angeles", 34.0259, -118.7798, 1650, "Malibu Coast"),
    "90401": ("Santa Monica",  "Los Angeles", 34.0195, -118.4912, 1200, "Downtown Santa Monica"),
    "91101": ("Pasadena",      "Los Angeles", 34.1478, -118.1445, 620,  "Old Pasadena"),
    "90802": ("Long Beach",    "Los Angeles", 33.7701, -118.1937, 480,  "Downtown Long Beach"),
    "92651": ("Laguna Beach",  "Orange",      33.5427, -117.7854, 1100, "Laguna Village"),
    "92660": ("Newport Beach", "Orange",      33.6189, -117.9298, 980,  "Newport Center"),
    "92101": ("San Diego",     "San Diego",   32.7157, -117.1611, 650,  "Downtown San Diego"),
    "91801": ("Alhambra",      "Los Angeles", 34.0953, -118.1270, 520,  "Alhambra"),
    "90745": ("Carson",        "Los Angeles", 33.8317, -118.2820, 410,  "Carson"),
}

STREET_NAMES = [
    "Ocean Ave", "Sunset Blvd", "Wilshire Blvd", "Colorado Blvd", "Main St",
    "Pine St", "Palm Dr", "Highland Ave", "Vista Del Mar", "Canyon Rd",
    "Elm St", "Rodeo Dr", "Broadway", "Coast Hwy", "Foothill Blvd",
]

PROPERTY_TYPE_WEIGHTS = [
    (PropertyType.SINGLE_FAMILY, 0.55),
    (PropertyType.CONDO, 0.20),
    (PropertyType.TOWNHOUSE, 0.10),
    (PropertyType.MULTI_FAMILY, 0.10),
    (PropertyType.VACANT_LAND, 0.05),
]

ZONING_FOR_TYPE = {
    PropertyType.SINGLE_FAMILY: ZoningType.RESIDENTIAL_LOW,
    PropertyType.CONDO: ZoningType.RESIDENTIAL_HIGH,
    PropertyType.TOWNHOUSE: ZoningType.RESIDENTIAL_MEDIUM,
    PropertyType.MULTI_FAMILY: ZoningType.RESIDENTIAL_HIGH,
    PropertyType.VACANT_LAND: ZoningType.RESIDENTIAL_LOW,
}

N_PROPERTIES = 300


def weighted_choice(pairs):
    types, weights = zip(*pairs)
    return random.choices(types, weights=weights, k=1)[0]


def make_property(owner_id: int) -> Property:
    zip_code, (city, county, base_lat, base_lng, base_psf, _) = random.choice(list(AREAS.items()))
    ptype = weighted_choice(PROPERTY_TYPE_WEIGHTS)

    building_sqft = random.randint(900, 5200) if ptype != PropertyType.VACANT_LAND else None
    lot_size_sqft = random.randint(2500, 15000)
    year_built = random.randint(1925, 2023)

    # price scales with area base $/sqft, size, and a little noise
    if building_sqft:
        intrinsic_value = building_sqft * base_psf * random.uniform(0.85, 1.25)
    else:
        intrinsic_value = lot_size_sqft * (base_psf * 0.15) * random.uniform(0.8, 1.3)

        # sale_price and list_price (via estimated_value below) are each
        # independent, noisy estimates of the same underlying intrinsic value —
        # NEITHER is derived from the other. Previously estimated_value was
        # generated as sale_price * small_noise, which directly leaked the
        # target into the AVM's two strongest engineered features
        # (price_vs_neighborhood, list_price_per_sqft) and produced an inflated,
        # not-real-world R² of ~0.99. Real listing prices are an agent/seller's
        # own imperfect estimate set *before* a sale happens, not a function of
        # the eventual sale price.
    sale_price = round(intrinsic_value * random.uniform(0.93, 1.07), 2)
    list_price_val = round(intrinsic_value * random.uniform(0.90, 1.12), -3)

    sale_date = datetime.utcnow() - timedelta(days=random.randint(0, 730))


    return Property(
        owner_id=owner_id,
        address=f"{random.randint(100, 9999)} {random.choice(STREET_NAMES)}",
        city=city,
        state="CA",
        zip_code=zip_code,
        county=county,
        latitude=base_lat + random.uniform(-0.03, 0.03),
        longitude=base_lng + random.uniform(-0.03, 0.03),
        property_type=ptype,
        zoning=ZONING_FOR_TYPE[ptype],
        lot_size_sqft=lot_size_sqft,
        building_sqft=building_sqft,
        year_built=year_built,
        bedrooms=random.choice([1, 2, 2, 3, 3, 3, 4, 4, 5]) if building_sqft else None,
        bathrooms=random.choice([1, 1, 2, 2, 2, 3, 3, 4]) if building_sqft else None,
        stories=random.choice([1, 1, 2, 2, 3]) if building_sqft else None,
        units=1 if ptype != PropertyType.MULTI_FAMILY else random.choice([2, 3, 4]),
        garage_spaces=random.choice([0, 1, 2, 2, 3]),
        pool=random.random() < 0.18,
        last_sale_price=sale_price,
        last_sale_date=sale_date,
        assessed_value=round(intrinsic_value * random.uniform(0.85, 1.0), 2),
        # A real column now (migration a1c9f3d2e7b4) - this used to go into
        # estimated_value as a workaround since list_price didn't exist.
        # estimated_value itself is intentionally left unset here: it's the
        # AVM's own prediction, not something seed data should pre-fill with
        # fabricated ground truth.
        list_price=list_price_val,
        price_per_sqft=round(sale_price / building_sqft, 2) if building_sqft else None,
        data_source="seed_synthetic",
        is_verified=True,
    )


def make_features(property_id: int, zip_code: str, ptype: PropertyType) -> PropertyFeature:
    _, _, _, _, base_psf, _ = AREAS[zip_code]
    affluence = min(base_psf / 1650, 1.0)  # 0-1 scale vs. most expensive area

    return PropertyFeature(
        property_id=property_id,
        lot_to_building_ratio=round(random.uniform(1.5, 6.0), 2),
        age_years=random.randint(1, 100),
        price_per_sqft=round(base_psf * random.uniform(0.85, 1.2), 2),
        walk_score=int(random.uniform(30, 60) + affluence * 30),
        transit_score=int(random.uniform(20, 50) + affluence * 20),
        bike_score=int(random.uniform(25, 55) + affluence * 15),
        median_income=round(60000 + affluence * 180000, -3),
        crime_index=round(random.uniform(10, 60) * (1 - affluence * 0.5), 1),
        school_rating=round(min(10, random.uniform(4, 8) + affluence * 3), 1),
        distance_to_downtown_mi=round(random.uniform(1, 35), 1),
        distance_to_transit_mi=round(random.uniform(0.1, 5), 1),
        flood_zone=random.choice(["X", "X", "X", "AE"]),
        fire_hazard_zone=random.choice(["none", "none", "moderate", "high"]),
        max_allowed_units=1 if ptype == PropertyType.SINGLE_FAMILY else random.choice([2, 4, 8]),
        far_ratio=round(random.uniform(0.3, 1.5), 2),
        setback_front_ft=round(random.uniform(10, 25), 1),
        setback_rear_ft=round(random.uniform(5, 20), 1),
        height_limit_ft=random.choice([25, 30, 35, 45]),
        adu_eligible=ptype == PropertyType.SINGLE_FAMILY and random.random() < 0.65,
        adu_max_sqft=random.choice([600, 800, 1000]) if ptype == PropertyType.SINGLE_FAMILY else None,
        development_score=round(random.uniform(20, 95), 1),
    )


def make_neighborhoods() -> list[Neighborhood]:
    result = []
    for zip_code, (city, county, _, _, base_psf, name) in AREAS.items():
        median_price = base_psf * random.uniform(1400, 2200)
        result.append(Neighborhood(
            zip_code=zip_code,
            city=city,
            county=county,
            neighborhood_name=name,
            median_home_price=round(median_price, -3),
            median_price_sqft=round(base_psf * random.uniform(0.9, 1.1), 2),
            avg_days_on_market=random.randint(18, 75),
            inventory_count=random.randint(15, 220),
            months_of_supply=round(random.uniform(1.0, 5.5), 1),
            price_change_yoy=round(random.uniform(-3.0, 9.0), 2),
            price_change_mom=round(random.uniform(-1.0, 1.5), 2),
            population=random.randint(15000, 95000),
            median_income=round(55000 + (base_psf / 1650) * 190000, -3),
            median_age=round(random.uniform(32, 48), 1),
            owner_occupied_pct=round(random.uniform(35, 75), 1),
            renter_occupied_pct=round(random.uniform(25, 65), 1),
            avg_school_rating=round(random.uniform(5, 9.5), 1),
            walk_score=random.randint(35, 92),
            transit_score=random.randint(20, 75),
            restaurant_score=random.randint(30, 95),
            park_count=random.randint(2, 25),
            new_permits_ytd=random.randint(5, 120),
            adu_permits_ytd=random.randint(0, 40),
            commercial_vacancy=round(random.uniform(2.0, 15.0), 1),
        ))
    return result


def make_market_trends(months: int = 24) -> list[MarketTrend]:
    result = []
    for zip_code, (_, _, _, _, base_psf, _) in AREAS.items():
        base_median = base_psf * 1800
        # gentle upward drift with monthly noise, most recent month last
        for m in range(months, 0, -1):
            snapshot_date = datetime.utcnow() - timedelta(days=m * 30)
            drift = 1 + (months - m) * 0.004  # ~0.4%/mo long-run appreciation
            median_price = base_median * drift * random.uniform(0.97, 1.03)
            result.append(MarketTrend(
                zip_code=zip_code,
                snapshot_date=snapshot_date,
                median_price=round(median_price, -2),
                active_listings=random.randint(15, 200),
                closed_sales=random.randint(5, 60),
                avg_dom=random.randint(18, 80),
                list_to_sale=round(random.uniform(0.94, 1.04), 3),
                absorption_rate=round(random.uniform(0.15, 0.6), 2),
            ))
    return result

def run_migrations():
    """Ensure the DB is on the latest schema via Alembic, instead of
    creating tables directly from current models (which bypasses
    migration history and never sets alembic_version)."""
    repo_root = Path(__file__).resolve().parents[2]  # data_layer/seeds/ -> repo root
    alembic_cfg = Config(str(repo_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(repo_root / "migrations"))
    command.upgrade(alembic_cfg, "head")

def run():
    engine = get_engine(DATABASE_URL)
    run_migrations()
    session = get_session(engine)

    try:
        print("Seeding users...")
        owner = User(
            email="owner@propiq.dev", full_name="Demo Owner",
            password_hash="not_a_real_hash", role=UserRole.CLIENT,
        )
        manager = User(
            email="manager@propiq.dev", full_name="Demo Manager",
            password_hash="not_a_real_hash", role=UserRole.MANAGER,
        )
        analyst = User(
            email="analyst@propiq.dev", full_name="Demo Analyst",
            password_hash="not_a_real_hash", role=UserRole.ANALYST,
        )
        session.add_all([owner, manager, analyst])
        session.flush()  # get their ids without committing yet

        print(f"Seeding {N_PROPERTIES} properties + features + price history...")
        properties = []
        for _ in range(N_PROPERTIES):
            prop = make_property(owner_id=owner.id)
            session.add(prop)
            properties.append(prop)
        session.flush()  # get property ids

        for prop in properties:
            session.add(make_features(prop.id, prop.zip_code, prop.property_type))
            session.add(PriceHistory(
                property_id=prop.id,
                event_type="sale",
                price=prop.last_sale_price,
                price_sqft=prop.price_per_sqft,
                date=prop.last_sale_date,
                source="seed_synthetic",
            ))
            # a rough AVM-style valuation so the frontend has something to show
            predicted = prop.last_sale_price * random.uniform(0.95, 1.15)
            session.add(PropertyValuation(
                property_id=prop.id,
                model_name="avm_seed_placeholder",
                model_version="0.0",
                model_type="xgboost",
                predicted_value=round(predicted, 2),
                predicted_score=round(random.uniform(0.5, 0.95), 2),
                value_lower_bound=round(predicted * 0.92, 2),
                value_upper_bound=round(predicted * 1.08, 2),
                recommended_additions=[],
                development_potential={},
                roi_projections={},
                feature_importances={},
                top_value_drives=[],
            ))

        print("Seeding neighborhoods...")
        session.add_all(make_neighborhoods())

        print("Seeding market trends (24 months x 10 zips)...")
        session.add_all(make_market_trends(months=24))

        print("Seeding a demo project + tasks for the Kanban board...")
        demo_project = Project(
            property_id=properties[0].id,
            manager_id=manager.id,
            client_id=owner.id,
            title=f"ADU build — {properties[0].address}",
            description="Demo renovation project seeded for Kanban board testing.",
            status=ProjectStatus.IN_PROGRESS,
            project_type="ADU build",
            budget=185000,
            spent=42000,
            estimated_value_add=210000,
            start_date=datetime.utcnow() - timedelta(days=45),
            target_end_date=datetime.utcnow() + timedelta(days=90),
            progress_pct=25.0,
        )
        session.add(demo_project)
        session.flush()

        task_defs = [
            ("Permit application", TaskStatus.DONE),
            ("Site survey", TaskStatus.DONE),
            ("Foundation work", TaskStatus.IN_PROGRESS),
            ("Framing", TaskStatus.TODO),
            ("Electrical rough-in", TaskStatus.TODO),
            ("Final inspection", TaskStatus.TODO),
        ]
        for title, status in task_defs:
            session.add(Task(
                project_id=demo_project.id,
                assignee_id=analyst.id,
                title=title,
                status=status,
                priority=2,
                due_date=datetime.utcnow() + timedelta(days=random.randint(5, 60)),
            ))

        session.add(Milestone(
            project_id=demo_project.id,
            title="Foundation complete",
            due_date=datetime.utcnow() + timedelta(days=14),
            is_completed=False,
        ))

        session.commit()
        print(f"Done. Seeded {N_PROPERTIES} properties across {len(AREAS)} zip codes.")

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    run()