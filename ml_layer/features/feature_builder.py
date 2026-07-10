"""
    PropIQ - Feature Builder
    Converts raw Property + Neighborhood rows into a clean feature matrix
    for both training and real-time inference.

    Features groups:
        - structural: sqft, beds, baths, age, lot ratio, stories
        - location: lat/lon, walk score, transit score, school rating, zip embeddings
        - market: days_on_market, price_per_sqft, list_price_delta, neighborhood trend
        - opportunity: development_score, adu_eligible, underbuilt_ratio
        - temporal: month, quarter, year (for seasonality)

    @author Minh Thang Nguyen
    @version June 22, 2026
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional
from loguru import logger

STRUCTURAL_COLS = [
    "building_sqft", "lot_size_sqft", "bedrooms", "bathrooms",
    "year_built", "property_age", "stories", "lot_to_building_ratio", "sqft_per_bed",
]

LOCATION_COLS = [
    "latitude", "longitude", "walk_score", "transit_score",
    "school_rating", "distance_to_cbd_miles", "distance_to_coast_miles",
]

MARKET_COLS = [
    "days_on_market", "list_price_per_sqft", "price_change_yoy",
    "neighborhood_median_price", "price_vs_neighborhood", "inventory_months", "absorption_rate",
]

OPPORTUNITY_COLS = [
    "development_score", "adu_eligible", "underbuilt_ratio", "renovation_score",
]

TEMPORAL_COLS = [
    "list_month", "list_quarter", "list_year_normalized",
]

ALL_FEATURE_COLS = STRUCTURAL_COLS + LOCATION_COLS + MARKET_COLS + OPPORTUNITY_COLS + TEMPORAL_COLS
TARGET_COL = 'sale_price'

class FeatureBuilder:
    def __init__(self, current_year: Optional[int] = None):
        self.current_year = current_year or datetime.utcnow().year
        self._zip_median_cache: dict[str, float] = {}

    def build(self, df: pd.DataFrame, target: Optional[str] = None) \
        -> tuple[pd.DataFrame, Optional[pd.Series]]:
        df = df.copy()
        df = self._structural_features(df)
        df = self._location_features(df)
        df = self._market_features(df)
        df = self._opportunity_features(df)
        df = self._temporal_features(df)

        missing = [c for c in ALL_FEATURE_COLS if c not in df.columns]
        if missing:
            logger.warning(f'FeatureBuilder: {len(missing)} columns missing, filling 0: {missing[:5]}...')
            for c in missing:
                df[c] = 0.0

        X = df[ALL_FEATURE_COLS].astype(float)
        y = df[target].astype(float) if target and target in df.columns else None
        return X, y

    def feature_name(self) -> list[str]:
        return ALL_FEATURE_COLS

    def _structural_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df['property_age'] = self.current_year - df.get('year_built', self.current_year - 30)
        bsqft = df.get("building_sqft", pd.Series(np.nan, index=df.index)).fillna(1500)
        lsqft = df.get("lot_size_sqft", pd.Series(np.nan, index=df.index)).fillna(6000)
        beds = df.get("bedrooms", pd.Series(3, index=df.index)).fillna(3)
        df['building_sqft'] = bsqft
        df['lot_size_sqft'] = lsqft
        df['bedrooms'] = beds
        df['bathrooms'] = df.get('bathrooms', pd.Series(2.0, index=df.index)).fillna(2.0)
        df['stories'] = df.get('stories', pd.Series(1, index=df.index)).fillna(1)
        df['lot_to_building_ratio'] = (lsqft / bsqft.clip(lower=1)).clip(upper=20)
        df['sqft_per_bed'] = (bsqft / beds.clip(lower=1)).clip(upper=2000)
        return df

    def _location_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df['latitude'] = df.get('latitude', pd.Series(34.05, index=df.index)).fillna(34.05)
        df['longitude'] = df.get('longitude', pd.Series(-118.2, index=df.index)).fillna(-118.2)
        df['walk_score'] = df.get('walk_score', pd.Series(50.0, index=df.index)).fillna(50.0)
        df['transit_score'] = df.get('transit_score', pd.Series(40.0, index=df.index)).fillna(40.0)
        df['school_rating'] = df.get('school_rating', pd.Series(6.0, index=df.index)).fillna(6.0)
        df['distance_to_cbd_miles'] = df.get('distance_to_cbd_miles', pd.Series(10.0, index=df.index)).fillna(10.0)
        df['distance_to_coast_miles'] = df.get('distance_to_coast_miles', pd.Series(15.0, index=df.index)).fillna(15.0)
        return df

    def _market_features(self, df: pd.DataFrame) -> pd.DataFrame:
        bsqft = df['building_sqft'].clip(lower=1)
        lp = df.get('list_price', pd.Series(np.nan, index=df.index)).fillna(0)
        df['days_on_market'] = df.get('days_on_market', pd.Series(30, index=df.index)).fillna(30)
        df['list_price_per_sqft'] = (lp / bsqft).clip(upper=2000)
        df['price_change_yoy'] = df.get('price_change_yoy', pd.Series(3.0, index=df.index)).fillna(3.0)
        df['neighborhood_median_price'] = df.get('neighborhood_median_price',
                                                 pd.Series(800000, index=df.index)).fillna(800000)
        df['price_vs_neighborhood'] = (lp / df['neighborhood_median_price']).clip(lower=1).clip(upper=5)
        df['inventory_months'] = df.get('inventory_months', pd.Series(2.5, index=df.index)).fillna(2.5)
        df['absorption_rate'] = df.get('absorption_rate', pd.Series(0.4, index=df.index)).fillna(0.4)
        return df

    def _opportunity_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df['development_score'] = df.get('development_score', pd.Series(50.0, index=df.index)).fillna(50.0)
        df['adu_eligible'] = df.get('adu_eligible', pd.Series(0, index=df.index)).fillna(0).astype(int)
        df['underbuilt_ratio'] = df.get('underbuilt_ratio', pd.Series(0.3, index=df.index)).fillna(0.3)
        df['renovation_score'] = self._renovation_score(df)
        return df

    def _renovation_score(self, df: pd.DataFrame) -> pd.Series:
        age_score = (df['property_age'].clip(0, 100) / 100 * 50)
        cond_score = (10 - df.get('condition_rating', pd.Series(7, index=df.index)).fillna(7)) / 10 * 50
        return (age_score + cond_score).clip(0, 100)

    def _temporal_features(self, df: pd.DataFrame) -> pd.DataFrame:
        now = datetime.utcnow()
        list_date = pd.to_datetime(df.get('list_date', now), errors='coerce').fillna(now)
        df['list_month'] = list_date.dt.month
        df['list_quarter'] = list_date.dt.quarter
        df['list_year_normalized'] = list_date.dt.year - 2010
        return df

