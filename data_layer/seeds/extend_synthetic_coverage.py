"""
    PropIQ - Extend Synthetic Coverage
    Extends the SAME synthetic generators seed_avm_data.py and
    seed_market_history.py already use for the ~20 SEED_ZIPS - just to
    every real OC zip/parcel instead. Nothing here is real data; every
    value is procedurally generated exactly like the original synthetic
    seed data, disclaimed the same way everywhere else in the app
    ("Synthetic demo data" / "AI-generated ... not an appraisal").

    Fills in, for every real (data_source='oc_parcel_gis') zip/property
    that doesn't already have one:
      - Neighborhood row (walk/transit/school context, price level)
      - 60 months of MarketTrend history (feeds the LSTM forecast)
      - PropertyFeature row (walk/transit/bike/school scores, ADU
        eligibility, development score)

    PRICE INTEGRITY: a zip's price level here is ALWAYS anchored to a real
    Zillow-derived average for that zip - never a fabricated placeholder.
    A zip with zero real price data gets SKIPPED (no Neighborhood, no
    MarketTrend, forecast stays honestly "unavailable" for it) rather than
    seeded with a made-up number. This matters because that price anchor
    feeds the LSTM forecast chart, and this project's whole design
    principle has been: real market price, real ML prediction, or an
    honest "unavailable" - never a fabricated figure. (Property.list_price
    and Property.estimated_value, what "Current estimate"/"List price"
    actually display, are untouched by this script either way - those only
    ever come from the real AVM or real Zillow ingestion.)

    PropertyFeature (walk/transit/school/etc - NOT price) gracefully
    degrades for real parcels missing building_sqft/lot_size_sqft/
    year_built (most of them): fields that genuinely can't be computed
    without those (age_years, lot_to_building_ratio, adu_eligible) are
    left NULL/False rather than guessed, same as the AVM's own gate.

    Usage:
        python -m data_layer.seeds.extend_synthetic_coverage
        python -m data_layer.seeds.extend_synthetic_coverage --dry-run
        python -m data_layer.seeds.extend_synthetic_coverage --feature-batch-size 5000

    @author Minh Thang Nguyen
    @version August 7, 2026
"""

from __future__ import annotations

import os
import random
import argparse
import logging

from sqlalchemy import text, select

from data_layer.models.database import (
    PropertyFeature, Neighborhood, MarketTrend,
    PropertyType, get_engine, get_session,
)
from data_layer.seeds.seed_market_history import _generate_zip_series

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.seeds.extend_coverage')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')
REAL_SOURCE = 'oc_parcel_gis'
MARKET_HISTORY_MONTHS = 60

# No fallback price. A zip with zero real Zillow-derived pricing gets
# SKIPPED, not seeded with a made-up number - price must come from a real
# market or the trained ML model, never fabricated. This means some real
# OC zips will legitimately have no forecast available, same as before.


def _zips_needing_neighborhood(session) -> list[str]:
    real_zips = {
        row[0] for row in session.execute(
            text("SELECT DISTINCT zip_code FROM properties WHERE data_source = :src"),
            {"src": REAL_SOURCE},
        )
    }
    existing = {
        row[0] for row in session.execute(select(Neighborhood.zip_code))
    }
    return sorted(real_zips - existing)


def _zip_anchor_stats(session, zip_code: str) -> dict | None:
    """Real average price for this zip, from actual Zillow-enriched rows
    only. Returns None if the zip has no real price data at all - callers
    must skip it rather than invent a number."""
    row = session.execute(
        text("""
            SELECT
                AVG(COALESCE(list_price, estimated_value)) AS avg_price,
                (SELECT city FROM properties WHERE zip_code = :zc LIMIT 1) AS city,
                (SELECT county FROM properties WHERE zip_code = :zc LIMIT 1) AS county
            FROM properties
            WHERE zip_code = :zc AND data_source = :src
              AND (list_price IS NOT NULL OR estimated_value IS NOT NULL)
        """),
        {"zc": zip_code, "src": REAL_SOURCE},
    ).mappings().first()

    if not row or row["avg_price"] is None:
        return None

    return {
        "base_price": float(row["avg_price"]),
        "city": row["city"] or zip_code,
        "county": row["county"] or "Orange",
    }


def extend_neighborhoods_and_history(session, rng: random.Random, months: int, dry_run: bool) -> dict:
    zips = _zips_needing_neighborhood(session)
    logger.info(f"[extend] {len(zips)} real zips have no Neighborhood row yet")

    stats = {"zips": 0, "skipped_no_real_price": 0, "market_rows": 0}
    for zip_code in zips:
        anchor = _zip_anchor_stats(session, zip_code)
        if anchor is None:
            # No real price signal for this zip at all - leave forecast
            # honestly "unavailable" rather than fabricate a starting
            # point for it.
            stats["skipped_no_real_price"] += 1
            continue

        # Walk/transit/school context (not price) - no hand-curated local
        # knowledge for these zips like the original 20 SEED_ZIPS, so
        # honest random ranges rather than invented specifics.
        walk = rng.randint(25, 85)
        transit = rng.randint(15, 70)
        school = round(rng.uniform(3.5, 8.5), 1)

        if not dry_run:
            session.add(Neighborhood(
                zip_code=zip_code, city=anchor["city"], county=anchor["county"],
                neighborhood_name=f'{anchor["city"]} {zip_code}',
                median_home_price=anchor["base_price"],  # real Zillow-derived average
                median_price_sqft=round(anchor["base_price"] / 1900, 2),
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
            for row in _generate_zip_series(rng, zip_code, anchor["base_price"], months):
                session.add(MarketTrend(**row))
                stats["market_rows"] += 1

        stats["zips"] += 1

    if not dry_run:
        session.commit()
    return stats


def extend_property_features(session, rng: random.Random, batch_size: int, dry_run: bool) -> dict:
    # Every real property that doesn't already have a PropertyFeature row.
    # This is context data (walk/transit/school), not price - not gated
    # the same way, but still gracefully degrades where physical
    # attributes are missing rather than guessing at derived fields.
    rows = session.execute(text("""
        SELECT p.id, p.zip_code, p.property_type, p.building_sqft, p.lot_size_sqft, p.year_built
        FROM properties p
        LEFT JOIN property_features pf ON pf.property_id = p.id
        WHERE p.data_source = :src AND pf.id IS NULL
    """), {"src": REAL_SOURCE}).fetchall()

    logger.info(f"[extend] {len(rows)} real properties need a PropertyFeature row")
    if not rows:
        return {"properties": 0}

    zip_context = {
        r.zip_code: (r.walk_score, r.transit_score, r.avg_school_rating)
        for r in session.execute(select(Neighborhood.zip_code, Neighborhood.walk_score,
                                         Neighborhood.transit_score, Neighborhood.avg_school_rating))
    }

    if dry_run:
        return {"properties": len(rows)}

    written = 0
    batch: list[dict] = []
    for r in rows:
        walk, transit, school = zip_context.get(r.zip_code, (50, 40, 6.0))

        has_building = bool(r.building_sqft and r.building_sqft > 0)
        has_lot = bool(r.lot_size_sqft and r.lot_size_sqft > 0)
        has_year = bool(r.year_built and r.year_built > 1800)

        batch.append(dict(
            property_id=r.id,
            lot_to_building_ratio=round(r.lot_size_sqft / r.building_sqft, 3) if has_building and has_lot else None,
            age_years=(2026 - r.year_built) if has_year else None,
            price_per_sqft=None,  # never fabricated - real value already lives on Property itself
            walk_score=min(100, max(0, walk + rng.randint(-8, 8))),
            transit_score=min(100, max(0, transit + rng.randint(-8, 8))),
            bike_score=rng.randint(30, 90),
            school_rating=min(10, max(1, school + rng.uniform(-0.7, 0.7))),
            distance_to_downtown_mi=round(rng.uniform(1.5, 35.0), 1),
            distance_to_transit_mi=round(rng.uniform(0.1, 4.0), 2),
            adu_eligible=bool(
                r.property_type == PropertyType.SINGLE_FAMILY.name and has_lot and r.lot_size_sqft > 5500
            ),
            adu_max_sqft=min(1200, int(r.building_sqft * 0.5)) if has_building else None,
            development_score=round(rng.uniform(20, 85), 1),
        ))

        if len(batch) >= batch_size:
            session.bulk_insert_mappings(PropertyFeature, batch)
            session.commit()
            written += len(batch)
            logger.info(f'[extend] ...{written}/{len(rows)} PropertyFeature rows written')
            batch = []

    if batch:
        session.bulk_insert_mappings(PropertyFeature, batch)
        session.commit()
        written += len(batch)

    return {"properties": written}


def run(database_url: str, months: int, feature_batch_size: int, dry_run: bool, rng_seed: int = 42) -> dict:
    rng = random.Random(rng_seed)
    engine = get_engine(database_url)

    with get_session(engine) as session:
        neighborhood_stats = extend_neighborhoods_and_history(session, rng, months, dry_run)

    with get_session(engine) as session:
        feature_stats = extend_property_features(session, rng, feature_batch_size, dry_run)

    logger.info(
        f"[extend] done{' (DRY RUN)' if dry_run else ''} - "
        f"{neighborhood_stats['zips']} zips got Neighborhood+history (real-price-anchored only), "
        f"{neighborhood_stats.get('skipped_no_real_price', 0)} zips skipped (no real price signal), "
        f"{neighborhood_stats.get('market_rows', 0)} MarketTrend rows, "
        f"{feature_stats['properties']} PropertyFeature rows"
    )
    return {**neighborhood_stats, **feature_stats}


def main():
    parser = argparse.ArgumentParser(
        description='Extend synthetic details/forecast coverage to every real OC zip/parcel with a real price anchor.'
    )
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    parser.add_argument('--months', type=int, default=MARKET_HISTORY_MONTHS)
    parser.add_argument('--feature-batch-size', type=int, default=5000)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    run(args.database_url, args.months, args.feature_batch_size, args.dry_run, args.seed)


if __name__ == '__main__':
    main()