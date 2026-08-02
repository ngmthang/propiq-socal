"""
    PropIQ - Synthetic Seed Data Generator
    Generates AVM-trainable synthetic sold properties across PropIQ's SoCal
    zips, plus matching Neighborhood rows and PropertyFeature rows.

    Why synthetic sales at all: CA doesn't publicly disclose real sale
    prices, so the platform's real county-parcel data (data_source=
    'oc_parcel_gis') can never carry training targets. Seed rows exist
    solely to give the AVM a coherent, leakage-free training corpus.

    ANTI-LEAKAGE DESIGN (the July 2026 lesson, rebuilt):
        A hidden `intrinsic_value` is computed from physical features and
        the neighborhood price level. Then last_sale_price and
        estimated_value are derived INDEPENDENTLY from intrinsic_value,
        each with its own noise draw. They correlate with the features
        (learnable signal) but are not derived from each other - deriving
        estimated_value from sale_price previously produced a near-trivial
        R^2 of 0.987. Honest expectation with this design: R^2 ~ 0.95-0.97.

    Idempotency: synthetic rows have no identity worth preserving, so the
    script DELETEs prior seed_synthetic rows (and their children) and
    reseeds from scratch. Real data (oc_parcel_gis etc.) is never touched.

    Usage:
        python -m data_layer.seeds.seed_avm_data                # default 4000
        python -m data_layer.seeds.seed_avm_data --count 8000
        python -m data_layer.seeds.seed_avm_data --seed 7       # different RNG

    @author Minh Thang Nguyen
    @version August 2, 2026
"""

import os
import math
import random
import argparse
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, delete

from data_layer.models.database import (
    Property, PropertyFeature, PriceHistory, Neighborhood, User, UserRole,
    PropertyType, ZoningType, get_engine, get_session,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.seeds')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')
SEED_SOURCE = 'seed_synthetic'
SEED_OWNER_EMAIL = 'seed-data@propiq.internal'

# zip: (city, county, base price level $, lat, lon, walk, transit, school)
# Price levels are rough 2026-vibe medians per area; they only need to be
# internally consistent, not appraisal-grade.
SEED_ZIPS = {
    '90011': ('Los Angeles', 'Los Angeles', 620_000, 34.007, -118.258, 78, 65, 4.5),
    '90026': ('Los Angeles', 'Los Angeles', 1_150_000, 34.078, -118.264, 82, 60, 6.0),
    '90045': ('Los Angeles', 'Los Angeles', 1_250_000, 33.961, -118.396, 62, 45, 7.0),
    '90210': ('Beverly Hills', 'Los Angeles', 4_200_000, 34.090, -118.406, 55, 35, 8.5),
    '90230': ('Culver City', 'Los Angeles', 1_180_000, 34.003, -118.395, 70, 52, 7.5),
    '90266': ('Manhattan Beach', 'Los Angeles', 2_900_000, 33.888, -118.404, 68, 40, 9.0),
    '90291': ('Venice', 'Los Angeles', 1_950_000, 33.993, -118.464, 85, 50, 6.5),
    '90402': ('Santa Monica', 'Los Angeles', 3_400_000, 34.035, -118.503, 74, 55, 9.0),
    '90501': ('Torrance', 'Los Angeles', 890_000, 33.833, -118.313, 65, 42, 7.5),
    '90803': ('Long Beach', 'Los Angeles', 1_150_000, 33.760, -118.132, 72, 48, 7.0),
    '91344': ('Granada Hills', 'Los Angeles', 900_000, 34.288, -118.505, 45, 32, 7.5),
    '91423': ('Sherman Oaks', 'Los Angeles', 1_500_000, 34.150, -118.432, 66, 44, 7.5),
    '91604': ('Studio City', 'Los Angeles', 1_650_000, 34.143, -118.393, 68, 45, 8.0),
    '92602': ('Irvine', 'Orange', 1_350_000, 33.744, -117.772, 48, 35, 9.0),
    '92618': ('Irvine', 'Orange', 1_400_000, 33.669, -117.752, 50, 38, 9.5),
    '92627': ('Costa Mesa', 'Orange', 1_150_000, 33.648, -117.920, 63, 40, 7.0),
    '92648': ('Huntington Beach', 'Orange', 1_300_000, 33.678, -118.005, 60, 35, 8.0),
    '92660': ('Newport Beach', 'Orange', 2_800_000, 33.634, -117.874, 55, 30, 8.5),
    '92677': ('Laguna Niguel', 'Orange', 1_250_000, 33.556, -117.708, 42, 28, 8.5),
    '92805': ('Anaheim', 'Orange', 780_000, 33.830, -117.906, 62, 45, 5.5),
}

STREETS = ['Maple', 'Oak', 'Cedar', 'Palm', 'Sunset', 'Vista', 'Canyon', 'Harbor',
           'Pacific', 'Del Mar', 'Alamitos', 'Catalina', 'Sierra', 'Mesa', 'Laurel',
           'Magnolia', 'Juniper', 'Willow', 'Marina', 'Crescent']
SUFFIXES = ['Ave', 'St', 'Dr', 'Blvd', 'Ln', 'Ct', 'Way', 'Pl']

PROPERTY_TYPES = [
    (PropertyType.SINGLE_FAMILY, 0.62, ZoningType.RESIDENTIAL_LOW),
    (PropertyType.CONDO, 0.18, ZoningType.RESIDENTIAL_HIGH),
    (PropertyType.TOWNHOUSE, 0.12, ZoningType.RESIDENTIAL_MEDIUM),
    (PropertyType.MULTI_FAMILY, 0.08, ZoningType.RESIDENTIAL_MEDIUM),
]


def _pick_type(rng: random.Random):
    r, cum = rng.random(), 0.0
    for ptype, w, zone in PROPERTY_TYPES:
        cum += w
        if r <= cum:
            return ptype, zone
    return PROPERTY_TYPES[0][0], PROPERTY_TYPES[0][2]


def _intrinsic_value(rng, base_price, sqft, lot, beds, baths, year_built,
                     pool, garage, school, walk) -> float:
    """
    Hidden 'true value' of the home. This is the ONLY place value is
    computed from features; both observed prices derive from it with
    independent noise and never from each other.
    """
    # neighborhood level anchors ~60% of value; size drives most of the rest
    size_factor = (sqft / 1900) ** 0.85            # diminishing returns on size
    lot_factor = 1 + 0.10 * math.log1p(lot / 6000) # mild lot premium
    age = max(0, 2026 - year_built)
    age_factor = 1 - min(0.18, age * 0.003)        # older -> modestly cheaper
    room_factor = 1 + 0.03 * (beds - 3) + 0.04 * (baths - 2)
    amenity = 1 + (0.05 if pool else 0) + 0.015 * garage
    quality = 1 + 0.02 * (school - 6.5) + 0.001 * (walk - 60)
    idiosyncratic = rng.lognormvariate(0, 0.07)    # condition, staging, lot shape...

    return (base_price * 0.55 + base_price * 0.45 * size_factor) \
        * lot_factor * age_factor * room_factor * amenity * quality * idiosyncratic


def _make_property(rng: random.Random, zip_code: str, meta: tuple, idx: int) -> dict:
    city, county, base_price, lat0, lon0, walk, transit, school = meta
    ptype, zoning = _pick_type(rng)

    if ptype == PropertyType.CONDO:
        sqft = rng.randint(650, 1800); lot = 0.0; stories = 1
    elif ptype == PropertyType.TOWNHOUSE:
        sqft = rng.randint(1100, 2400); lot = rng.randint(1200, 3000); stories = 2
    elif ptype == PropertyType.MULTI_FAMILY:
        sqft = rng.randint(2400, 6500); lot = rng.randint(5000, 12000); stories = 2
    else:
        sqft = rng.randint(1100, 4200); lot = rng.randint(4000, 15000); stories = rng.choice([1, 1, 2])

    beds = max(1, min(6, round(sqft / 750) + rng.choice([-1, 0, 0, 1])))
    baths = max(1, min(5, round(sqft / 1000) + rng.choice([0, 0, 1])))
    year_built = rng.randint(1948, 2023)
    pool = rng.random() < (0.25 if ptype == PropertyType.SINGLE_FAMILY else 0.05)
    garage = rng.choice([0, 1, 2, 2, 2, 3])
    units = rng.randint(2, 4) if ptype == PropertyType.MULTI_FAMILY else 1

    # condo lot: share of common land, keep >0 so it passes the AVM-ready bar
    if lot == 0.0:
        lot = float(rng.randint(800, 1500))

    intrinsic = _intrinsic_value(rng, base_price, sqft, lot, beds, baths,
                                 year_built, pool, garage, school, walk)

    # INDEPENDENT observations of intrinsic value (never of each other):
    sale_price = round(intrinsic * rng.lognormvariate(0, 0.05), -3)   # negotiation, timing
    estimated_value = round(intrinsic * rng.lognormvariate(0, 0.06), -3)  # model/AVM-ish error
    assessed_value = round(intrinsic * rng.uniform(0.68, 0.85), -3)   # prop-13-ish lag

    sale_date = datetime.utcnow() - timedelta(days=rng.randint(5, 700))

    lat = lat0 + rng.uniform(-0.012, 0.012)
    lon = lon0 + rng.uniform(-0.012, 0.012)

    return {
        'address': f'{rng.randint(100, 9999)} {rng.choice(STREETS)} {rng.choice(SUFFIXES)}',
        'city': city, 'state': 'CA', 'zip_code': zip_code, 'county': county,
        'latitude': lat, 'longitude': lon,
        'parcel_number': f'SEED-{zip_code}-{idx:05d}',
        'property_type': ptype, 'zoning': zoning,
        'lot_size_sqft': float(lot), 'building_sqft': float(sqft),
        'year_built': year_built, 'bedrooms': beds, 'bathrooms': baths,
        'stories': stories, 'units': units, 'garage_spaces': garage, 'pool': pool,
        'last_sale_price': sale_price, 'last_sale_date': sale_date,
        'assessed_value': assessed_value, 'estimated_value': estimated_value,
        'price_per_sqft': round(estimated_value / sqft, 2),
        'data_source': SEED_SOURCE, 'source_url': '', 'is_verified': False,
        '_walk': walk, '_transit': transit, '_school': school,
    }


def _seed_neighborhoods(session, rng: random.Random):
    for zip_code, (city, county, base_price, lat, lon, walk, transit, school) in SEED_ZIPS.items():
        existing = session.execute(
            select(Neighborhood).where(Neighborhood.zip_code == zip_code)
        ).scalar_one_or_none()
        if existing:
            continue
        session.add(Neighborhood(
            zip_code=zip_code, city=city, county=county,
            neighborhood_name=f'{city} {zip_code}',
            median_home_price=float(base_price),
            median_price_sqft=round(base_price / 1900, 2),
            avg_days_on_market=rng.randint(18, 55),
            inventory_count=rng.randint(40, 300),
            months_of_supply=round(rng.uniform(1.2, 4.0), 1),
            price_change_yoy=round(rng.uniform(-2.0, 8.5), 1),
            price_change_mom=round(rng.uniform(-0.8, 1.2), 1),
            population=rng.randint(18_000, 65_000),
            median_income=float(rng.randint(55_000, 210_000)),
            median_age=round(rng.uniform(31, 47), 1),
            owner_occupied_pct=round(rng.uniform(35, 80), 1),
            renter_occupied_pct=round(rng.uniform(20, 65), 1),
            avg_school_rating=school, walk_score=walk, transit_score=transit,
        ))


def _get_or_create_seed_owner(session) -> int:
    owner = session.execute(
        select(User).where(User.email == SEED_OWNER_EMAIL)
    ).scalar_one_or_none()
    if owner:
        return owner.id
    owner = User(
        email=SEED_OWNER_EMAIL, full_name='Seed Data (System)',
        password_hash='!disabled!',
        role=UserRole.CLIENT,  # bots must never satisfy admin checks
        is_active=False,
    )
    session.add(owner)
    session.flush()
    return owner.id


def _clear_previous_seed(session):
    from sqlalchemy import update
    from data_layer.models.database import PropertyValuation, Project

    ids = [r[0] for r in session.execute(
        select(Property.id).where(Property.data_source == SEED_SOURCE)
    ).all()]
    if not ids:
        return 0

    # True children of a synthetic property die with it:
    session.execute(delete(PropertyValuation).where(PropertyValuation.property_id.in_(ids)))
    session.execute(delete(PriceHistory).where(PriceHistory.property_id.in_(ids)))
    session.execute(delete(PropertyFeature).where(PropertyFeature.property_id.in_(ids)))

    # Projects are USER-created work that merely points at a property -
    # deleting them would destroy real Kanban data, so detach instead.
    # (Requires Project.property_id to be nullable; it is - projects can
    # exist before being tied to a property.)
    session.execute(
        update(Project)
        .where(Project.property_id.in_(ids))
        .values(property_id=None)
    )

    session.execute(delete(Property).where(Property.id.in_(ids)))
    return len(ids)


def run(count: int, database_url: str, rng_seed: int = 42):
    rng = random.Random(rng_seed)
    engine = get_engine(database_url)
    per_zip = max(1, count // len(SEED_ZIPS))

    with get_session(engine) as session:
        removed = _clear_previous_seed(session)
        if removed:
            logger.info(f'[seeds] cleared {removed} previous seed properties')

        owner_id = _get_or_create_seed_owner(session)
        _seed_neighborhoods(session, rng)

        created = 0
        for zip_code, meta in SEED_ZIPS.items():
            for i in range(per_zip):
                data = _make_property(rng, zip_code, meta, created)
                walk, transit, school = data.pop('_walk'), data.pop('_transit'), data.pop('_school')

                prop = Property(owner_id=owner_id, **data)
                session.add(prop)
                session.flush()  # need prop.id for children

                session.add(PropertyFeature(
                    property_id=prop.id,
                    lot_to_building_ratio=round(prop.lot_size_sqft / prop.building_sqft, 3),
                    age_years=2026 - prop.year_built,
                    price_per_sqft=prop.price_per_sqft,
                    walk_score=min(100, max(0, walk + rng.randint(-8, 8))),
                    transit_score=min(100, max(0, transit + rng.randint(-8, 8))),
                    bike_score=rng.randint(30, 90),
                    school_rating=min(10, max(1, school + rng.uniform(-0.7, 0.7))),
                    distance_to_downtown_mi=round(rng.uniform(1.5, 35.0), 1),
                    distance_to_transit_mi=round(rng.uniform(0.1, 4.0), 2),
                    adu_eligible=(prop.property_type == PropertyType.SINGLE_FAMILY
                                  and prop.lot_size_sqft > 5500),
                    adu_max_sqft=min(1200, int(prop.building_sqft * 0.5)),
                    development_score=round(rng.uniform(20, 85), 1),
                ))
                session.add(PriceHistory(
                    property_id=prop.id, event_type='sale',
                    price=prop.last_sale_price,
                    price_sqft=round(prop.last_sale_price / prop.building_sqft, 2),
                    date=prop.last_sale_date, source=SEED_SOURCE,
                ))
                created += 1

        session.commit()

    logger.info(f'[seeds] created {created} properties across {len(SEED_ZIPS)} zips '
                f'(~{per_zip}/zip), all AVM-ready with sale prices in the last 24 months')
    return created


def main():
    parser = argparse.ArgumentParser(description='Generate leakage-free synthetic seed data.')
    parser.add_argument('--count', type=int, default=4000)
    parser.add_argument('--seed', type=int, default=42, help='RNG seed (reproducibility)')
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    args = parser.parse_args()
    run(args.count, args.database_url, args.seed)


if __name__ == '__main__':
    main()