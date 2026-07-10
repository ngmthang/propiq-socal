"""
    PropIQ - DB Utilities
    Bridges the data_layer Postgres schema into pandas DataFrame for ML training and inference.

    @author Minh Thang Nguyen
    @version July 8, 2026
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
            p.id, p.address, p.zip_code, p.latitude, p.longitude,
            p.property_type, p.bedrooms, p.bathrooms, p.building_sqft,
            p.lot_size_sqft, p.year_built, p.list_price, p.sale_price,
            p.list_date, p.sale_date, p.days_on_market,
            pf.walk_score, pf.transit_score, pf.school_rating,
            pf.development_score, pf.adu_eligible, pf.underbuilt_ratio,
            pf.distance_to_cbd_miles, pf.distance_to_coast_miles,
            n.median_price AS neighborhood_median_price,
            n.price_change_yoy,
            n.median_days_on_market AS days_on_market_avg,
            n.inventory_months, n.absorption_rate,
            n.name AS neighborhood_name
        FROM properties p
        LEFT JOIN property_features pf ON pf.property_id = p.id
        LEFT JOIN neighborhoods n ON n.zip_code = p.zip_code
        WHERE p.sale_price IS NOT NULL
            AND p.sale_date >= :cutoff
            AND p.sale_price > 50000
            AND p.sale_price < 50000000
        ORDER BY p.sale_date ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={'cutoff': cutoff})
    logger.info(f"load_training_data: {len(df)} rows (last {months} months")
    return df

def load_market_history(db_url: str, months: int = 36) -> pd.DataFrame:
    cutoff = datetime.utcnow() - timedelta(days=months * 30)
    engine = create_engine(db_url)
    query = text("""
        SELECT
            zip_code,
            DATE_TRUNC('month', period_start) AS month,
            median_price, median_price_per_sqft, inventory_count,
            days_on_market_avg, absorption_rate, new_listings,
            sold_count, list_to_sale_ratio
        FROM market_trends
        WHERE period_start >= :cutoff
        ORDER BY zip_code, month ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={'cutoff': cutoff})
    logger.info(f"load_market_history: {len(df)} rows across {df['zip_code'].unique()} ZIPs")
    return df

def load_property_for_inference(db_url: str, property_id: str) -> dict:
    engine = create_engine(db_url)
    query = text("""
        SELECT p.*
            pf.walk_score, pf.transit_score, pf.school_rating,
            pf.development_score, pf.adu_eligible, pf.underbuilt_ratio,
            pd.distance_to_cbd_miles, pf.distance_to_coast_miles,
            n.median_price AS neighborhood_median_price,
            n.price_change_yoy, n.inventory_months, n.absorption_rate,
            n.name AS neighborhood_name
        FROM properties p
        LEFT JOIN property_features pf ON pf.property_id = p.id
        LEFT JOIN neighborhoods n ON n.zip_code = p.zip_code
        WHERE p.id = :pid
        LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"pid": property_id}).mappings().fetchone()
    if row is None:
        raise ValueError(f"Property not found: {property_id}")
    return dict(row)

def load_comparables(db_url: str, zip_code: str, n: int = 5) -> list[dict]:
    engine = create_engine(db_url)
    query = text("""
        SELECT address, sale_price, building_sqft, sale_date
        FROM properties
        WHERE zip_code = :zip
            AND sale_price IS NOT NULL
            AND sale_date >= NOW() - INTERVAL '12 months'
        ORDER BY sale_date ASC
        LIMIT :n
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={'zip': zip_code, 'n': n})
    return df.rename(columns={'building_sqft': 'sqft', 'sale_date': 'date'}).to_dict('records')