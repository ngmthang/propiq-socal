"""
    PropIQ — ML Layer / DB Bridge

    Pulls Layer 1's actual Postgres schema (see data_layer/models/database.py)
    into DataFrames/dicts for training and inference.

    IMPORTANT: this file aliases real columns back to the field names the rest
    of ml_layer (feature_builder.py, avm_trainer.py, lstm_trainer.py) already
    expects, so nothing downstream needed to change. A few fields don't exist
    in the real schema at all — those come through as NULL, flagged below.
    Known gaps (fix later, not blocking):
      - list_date / days_on_market: no such columns on Property. list_date
        and days_on_market are NULL until Layer 1 starts tracking listing
        events separately from sale events. (list_price itself IS real now —
        Redfin ingestion populates Property.list_price directly; coalesced
        with estimated_value below so pre-ingestion zips still get a value.)
      - distance_to_coast_miles: not computed yet. Could derive from lat/long
        against a coastline reference point — not done here.
      - underbuilt_ratio: proxied by PropertyFeature.lot_to_building_ratio,
        which is the closest existing signal for development headroom.
      - Neighborhood has no absorption_rate column (only MarketTrend does) —
        pulled via a LATERAL join to each zip's most recent market_trends row.

    @author Minh Thang Nguyen
    @version July 15, 2026
"""

from __future__ import annotations

import pandas as pd
from datetime import datetime, timedelta
from loguru import logger
from sqlalchemy import create_engine, text


def load_training_data(db_url: str, months: int = 24) -> pd.DataFrame:
    cutoff = datetime.utcnow() - timedelta(days=months * 30)
    engine = create_engine(db_url)

    query = text("""
        SELECT
            p.id,
            p.address,
            p.zip_code,
            p.latitude,
            p.longitude,
            p.property_type,
            p.bedrooms,
            p.bathrooms,
            p.building_sqft,
            p.lot_size_sqft,
            p.year_built,

            p.estimated_value,
            COALESCE(p.list_price, p.estimated_value) AS list_price,
            p.last_sale_price      AS sale_price,
            NULL::timestamp        AS list_date,        -- not tracked yet
            p.last_sale_date       AS sale_date,
            NULL::integer          AS days_on_market,   -- not tracked yet

            pf.walk_score,
            pf.transit_score,
            pf.school_rating,
            pf.development_score,
            pf.adu_eligible,
            pf.lot_to_building_ratio   AS underbuilt_ratio,
            pf.distance_to_downtown_mi AS distance_to_cbd_miles,
            NULL::float                AS distance_to_coast_miles,  -- not computed yet

            n.median_home_price    AS neighborhood_median_price,
            n.price_change_yoy,
            n.avg_days_on_market   AS days_on_market_avg,
            n.months_of_supply     AS inventory_months,
            mt.absorption_rate,
            n.neighborhood_name
        FROM properties p
        LEFT JOIN property_features pf ON pf.property_id = p.id
        LEFT JOIN neighborhoods n      ON n.zip_code = p.zip_code
        LEFT JOIN LATERAL (
            SELECT absorption_rate
            FROM market_trends mt
            WHERE mt.zip_code = p.zip_code
            ORDER BY mt.snapshot_date DESC
            LIMIT 1
        ) mt ON true
        WHERE p.last_sale_price IS NOT NULL
          -- Explicit AVM-readiness bar (mirrors Property.is_avm_ready /
          -- AVM_REQUIRED_FIELDS): rows missing core physical features must
          -- not train the model on FeatureBuilder's imputation defaults.
          -- Until now this was only implicitly true because sale prices
          -- happened to exist only on complete seed rows; county parcel
          -- ingests (442k oc_parcel_gis rows with no sqft/bathrooms) make
          -- the implicit filter too fragile to rely on.
          AND p.building_sqft  IS NOT NULL AND p.building_sqft  > 0
          AND p.lot_size_sqft  IS NOT NULL AND p.lot_size_sqft  > 0
          AND p.bedrooms       IS NOT NULL AND p.bedrooms       > 0
          AND p.bathrooms      IS NOT NULL AND p.bathrooms      > 0
          AND p.latitude       IS NOT NULL
          AND p.longitude      IS NOT NULL
        ORDER BY p.last_sale_date ASC
    """)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"cutoff": cutoff})

    logger.info(f"load_training_data: {len(df)} rows (last {months} months)")
    return df

def load_scoring_data(db_url: str) -> pd.DataFrame:
    """
    Same feature set/columns as load_training_data, but deliberately
    WITHOUT the `last_sale_price IS NOT NULL` requirement - this is for
    batch inference (scoring properties with the already-trained model),
    not training, so properties we do NOT have a real price for are
    exactly the ones worth scoring. Still requires AVM_REQUIRED_FIELDS
    (physical attributes) so nothing gets scored off FeatureBuilder's
    imputation defaults.
    """
    engine = create_engine(db_url)

    query = text("""
        SELECT
            p.id,
            p.address,
            p.zip_code,
            p.latitude,
            p.longitude,
            p.property_type,
            p.bedrooms,
            p.bathrooms,
            p.building_sqft,
            p.lot_size_sqft,
            p.year_built,

            p.estimated_value,
            COALESCE(p.list_price, p.estimated_value) AS list_price,
            p.last_sale_price      AS sale_price,
            NULL::timestamp        AS list_date,
            p.last_sale_date       AS sale_date,
            NULL::integer          AS days_on_market,

            pf.walk_score,
            pf.transit_score,
            pf.school_rating,
            pf.development_score,
            pf.adu_eligible,
            pf.lot_to_building_ratio   AS underbuilt_ratio,
            pf.distance_to_downtown_mi AS distance_to_cbd_miles,
            NULL::float                AS distance_to_coast_miles,

            n.median_home_price    AS neighborhood_median_price,
            n.price_change_yoy,
            n.avg_days_on_market   AS days_on_market_avg,
            n.months_of_supply     AS inventory_months,
            mt.absorption_rate,
            n.neighborhood_name
        FROM properties p
        LEFT JOIN property_features pf ON pf.property_id = p.id
        LEFT JOIN neighborhoods n      ON n.zip_code = p.zip_code
        LEFT JOIN LATERAL (
            SELECT absorption_rate
            FROM market_trends mt
            WHERE mt.zip_code = p.zip_code
            ORDER BY mt.snapshot_date DESC
            LIMIT 1
        ) mt ON true
        WHERE p.building_sqft  IS NOT NULL AND p.building_sqft  > 0
          AND p.lot_size_sqft  IS NOT NULL AND p.lot_size_sqft  > 0
          AND p.bedrooms       IS NOT NULL AND p.bedrooms       > 0
          AND p.bathrooms      IS NOT NULL AND p.bathrooms      > 0
          AND p.latitude       IS NOT NULL
          AND p.longitude      IS NOT NULL
    """)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn)

    logger.info(f"load_scoring_data: {len(df)} AVM-ready rows")
    return df

def load_market_history(db_url: str, months: int = 36, zip_code: str | None = None) -> pd.DataFrame:
    """
    Returns: zip_code, month, median_price, inventory_count, etc.
    (feeds the LSTM forecaster)

    zip_code is optional: omit it for the full-table bulk load training
    needs (scheduler.py's retrain_lstm), or pass it to scope the query to
    one ZIP - which every per-request caller (market.py, properties.py)
    should do, since loading every ZIP's history just to filter down to
    one in Python doesn't scale.
    """
    cutoff = datetime.utcnow() - timedelta(days=months * 30)
    engine = create_engine(db_url)

    query = text(f"""
        SELECT
            zip_code,
            DATE_TRUNC('month', snapshot_date) AS month,
            median_price,
            NULL::float      AS median_price_per_sqft,  -- not tracked at this grain
            active_listings  AS inventory_count,
            avg_dom          AS days_on_market_avg,
            absorption_rate,
            NULL::integer    AS new_listings,            -- not tracked separately
            closed_sales     AS sold_count,
            list_to_sale     AS list_to_sale_ratio
        FROM market_trends
        WHERE snapshot_date >= :cutoff
        {"AND zip_code = :zip_code" if zip_code else ""}
        ORDER BY zip_code, month ASC
    """)

    params = {"cutoff": cutoff}
    if zip_code:
        params["zip_code"] = zip_code

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params=params)

    logger.info(f"load_market_history: {len(df)} rows across {df['zip_code'].nunique()} ZIPs")
    return df


def load_property_for_inference(db_url: str, property_id: int) -> dict:
    """
    Load a single property row with all features for inference.
    Returns a dict ready to pass into InferenceEngine.analyze_property().
    """
    engine = create_engine(db_url)

    query = text("""
        SELECT
            p.*,
            pf.walk_score, pf.transit_score, pf.school_rating,
            pf.development_score, pf.adu_eligible,
            pf.lot_to_building_ratio   AS underbuilt_ratio,
            pf.distance_to_downtown_mi AS distance_to_cbd_miles,
            NULL::float                AS distance_to_coast_miles,
            n.median_home_price    AS neighborhood_median_price,
            n.price_change_yoy,
            n.months_of_supply     AS inventory_months,
            n.neighborhood_name
        FROM properties p
        LEFT JOIN property_features pf ON pf.property_id = p.id
        LEFT JOIN neighborhoods n      ON n.zip_code = p.zip_code
        WHERE p.id = :pid
        LIMIT 1
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"pid": property_id})
        row = result.mappings().fetchone()

    if row is None:
        raise ValueError(f"Property not found: {property_id}")

    return dict(row)


def load_comparables(db_url: str, zip_code: str, n: int = 5) -> list[dict]:
    """
    Load N most recent comparable sales in the same ZIP.
    """
    engine = create_engine(db_url)

    query = text("""
        SELECT
            id, address,
            last_sale_price AS sale_price,
            last_sale_date  AS sale_date,
            building_sqft, bedrooms, bathrooms, price_per_sqft
        FROM properties
        WHERE zip_code = :zip_code
          AND last_sale_price IS NOT NULL
        ORDER BY last_sale_date DESC
        LIMIT :n
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"zip_code": zip_code, "n": n})
        rows = result.mappings().fetchall()

    return [dict(r) for r in rows]