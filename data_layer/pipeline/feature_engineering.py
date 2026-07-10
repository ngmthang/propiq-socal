"""
    PropIQ - Feature Engineering
    Computes and stores ML-ready features from raw Property + Neighborhood data.
    Run after each scrape cycle, before model training/inference.

    @author Minh Thang Nguyen
    @version June 20, 2026
"""

import math
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select

from data_layer.models.database import Property, PropertyFeature, Neighborhood

logger = logging.getLogger('propiq.features')

# Walk Score / Transit Score API (walkscore.com - free tier available)
WALK_SCORE_API_KEY = '' # set in .env

class FeatureEngineer:
    def __init__(self, walk_score_key: str = WALK_SCORE_API_KEY):
        self.walk_score_key = walk_score_key

    def compute_all(self, session: Session, batch_size: int = 500):
        """Compute features for all properties missing them."""
        props = session.execute(
            select(Property)
            .outerjoin(PropertyFeature)
            .where(PropertyFeature.id == None)
            .limit(batch_size)
        ).scalars().all()

        logger.info(f'[Features] Computing for {len(props)} properties')
        for prop in props:
            try:
                self.compute_for_property(session, prop)
            except Exception as e:
                logger.error(f'[Features] Error on property {prop.id}: {e}')

        session.commit()
        logger.info(f'[Features] Done')

    def compute_for_property(self, session: Session, prop: Property):
        """Build a PropertyFeature row for one property."""
        neighborhood = self._get_neighboorhood(session, prop.zip_code)
        feature = PropertyFeature(
            property_id = prop.id,

            # Derived Ratios
            lot_to_building_ratio = self._lot_ratio(prop),
            age_years = self._age(prop),
            price_per_sqft = self._price_sqft(prop),

            # Scores (from API or defaults)
            walk_score = self._fetch_walk_score(prop, 'walk'),
            transit_score = self._fetch_walk_score(prop, 'transit'),
            bike_score = self._fetch_walk_score(prop, 'bike'),

            # From Neighborhood Table
            median_income = neighborhood.median_income if neighborhood else None,
            school_rating = neighborhood.avg_school_rating if neighborhood else None,

            # Zoning Potential (Simplified Heuristics)
            adu_eligible = self._adu_eligible(prop),
            adu_max_sqft = self._adu_max_sqft(prop),
            max_allowed_units = self._max_units(prop),

            # Development Score: 0-100
            development_score = self._development_score(prop, neighborhood),

            computed_at = datetime.utcnow(),
        )

        session.add(feature)
        return feature

    # Features Helpers
    @staticmethod
    def _lot_ratio(prop: Property) -> float | None:
        if prop.lot_size_sqft and prop.building_sqft and prop.building_sqft > 0:
            return round(prop.lot_size_sqft / prop.building_sqft, 3)
        return None

    @staticmethod
    def _age(prop: Property) -> int | None:
        if prop.year_built:
            return datetime.utcnow().year - prop.year_built
        return None

    @staticmethod
    def _price_sqft(prop: Property) -> float | None:
        if prop.estimated_value and prop.building_sqft and prop.building_sqft > 0:
            return round(prop.estimated_value / prop.building_sqft, 2)
        return None

    @staticmethod
    def _fetch_walk_score(self, prop: Property, score_type: str) -> int | None:
        """
        Calls walkscore.com API.
        Returns score (0-100) or None if unavailable.
        In production, cache these - they don't change often.
        """
        if not (self.walk_score_key and prop.latitude and prop.longitude):
            return None

        import requests
        try:
            resp = requests.get(
                'https://api.walkscore.com/score',
                params={
                    'format': 'json',
                    'wsapikey': self.walk_score_key,
                    'lat': prop.latitude,
                    'lon': prop.longitude,
                    'address': prop.address,
                    'transit': 1,
                    'bike': 1,
                },
                timeout=5,
            )
            data = resp.json()
            if score_type == 'walk': return data.get('walkscore')
            if score_type == 'transit': return data.get('transit', {}).get('score')
            if score_type == 'bike': return data.get('bike', {}).get('score')
        except Exception:
            pass
        return None

    @staticmethod
    def _adu_eligible(prop: Property) -> bool:
        """
        California AB 68 / SB 9 - most single-family + multi-family
        residential lots are now ADU-eligible. Basic heuristics
        """
        if prop.property_type and \
           prop.property_type.value in ('single-family', 'multi-family', 'condo'):
            if prop.lot_size_sqft and prop.lot_size_sqft >= 1200:
                return True
        return False

    @staticmethod
    def _adu_max_sqft(prop: Property) -> float | None:
        """
        CA law caps ADUs at 1,200 sqft OR 50% or primary dwelling.
        """
        if not prop.building_sqft:
            return 1200
        return min(1200, int(prop.building_sqft * 0.5))

    @staticmethod
    def _max_units(prop: Property) -> int | None:
        """Rough heuristic from lot size and zoning."""
        if not prop.lot_size_sqft:
            return None
        # Very simplified - real version uses actual zoning API
        if prop.lot_size_sqft >= 10000: return 4
        if prop.lot_size_sqft >= 6000: return 2
        return 1

    @staticmethod
    def _development_score(prop: Property, neighborhood) -> float:
        """
        0-100 composite score indicating development/value-add potential.
        Higher = more opportunity.
        Weights:
            lot ratio (30%) - underbuilt lot = opportunity
            age (20%) - older = renovation potential
            ADU eligible (20%)
            neighborhood trend (30%) - YoY price growth
        """
        score = 0.0

        # Lot underutilization
        if prop.lot_size_sqft and prop.building_sqft:
            ratio = prop.lot_size_sqft / prop.building_sqft
            score += (1 - min(ratio, 1.0)) * 30 # 0-30 pts

        # Age
        if prop.year_built:
            age = datetime.utcnow().year - prop.year_built
            score += min(age / 100, 1.0) * 20 # 0-20 pts

        # ADU
        if FeatureEngineer._adu_eligible(prop):
            score += 20

        # Neighborhood Trend
        if neighborhood and neighborhood.price_change_yoy:
            growth = neighborhood.price_change_yoy # e.g. 5.2 = 5.2%
            score += min(max(growth / 20, 0), 1.0) * 30 # 0-30 pts

        return round(score, 1)

    @staticmethod
    def _get_neighborhood(session: Session, zip_code: str):
        return session.execute(
            select(Neighborhood).where(Neighborhood.zip_code == zip_code)
        ).scalar_one_or_none()

    # Distance Helpers
    def haversine_miles(lat1, lon1, lat2, lon2) -> float:
        """Great-circle distance in miles."""
        R = 3958.8 # Earth radius in miles
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

