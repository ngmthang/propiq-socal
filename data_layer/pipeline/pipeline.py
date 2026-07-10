"""
    PropIQ - Data Pipeline
    Orchestrates scraping -> cleaning -> deduplication -> database upsert.
    Run manually or scheduled via APScheduler / Celery.

    @author Minh Thang Nguyen
    @version June 21, 2026
"""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from data_layer.models.database import (
    Property, PropertyType, PriceHistory, ScrapeJob, get_session, get_engine
)
from data_layer.scrapers.zillow_scraper import ZillowScraper
from data_layer.scrapers.redfin_scraper import RedfinScraper, LACountyAssessorScraper
from .feature_engineering import FeatureEngineer

logger = logging.getLogger('propiq.pipeline')

# Socal zip codes we track
SOCAL_ZIP_CODES = {
    "Los Angeles":  ["90001","90002","90003","90004","90005","90006","90007",
                     "90008","90010","90011","90012","90013","90014","90015",
                     "90016","90017","90018","90019","90020","90021","90024",
                     "90025","90026","90027","90028","90029","90031","90032",
                     "90033","90034","90035","90036","90037","90038","90039",
                     "90041","90042","90043","90044","90045","90046","90047",
                     "90048","90049","90057","90058","90059","90061","90062",
                     "90063","90064","90065","90066","90067","90068","90069",
                     "90071","90073","90077","90089","90094","90095","90210",
                     "90211","90212","90230","90232","90245","90247","90248",
                     "90249","90254","90260","90262","90265","90272","90274",
                     "90275","90277","90278","90280","90290","90291","90292",
                     "90293","90301","90302","90303","90304","90305","90401",
                     "90402","90403","90404","90405"],
    "Irvine":       ["92602","92603","92604","92606","92612","92614","92617",
                     "92618","92620","92697"],
    "Long Beach":   ["90755","90802","90803","90804","90805","90806","90807",
                     "90808","90810","90813","90814","90815","90840"],
    "San Fernando": ["91340","91342","91343","91344","91345","91352","91356",
                     "91364","91367","91401","91402","91403","91405","91406",
                     "91411","91423","91436","91501","91502","91504","91505",
                     "91506","91601","91602","91604","91605","91606","91607"],
    "South Bay":    ["90250","90260","90261","90266","90501","90502","90503",
                     "90504","90505","90506","90710","90717","90731","90732",
                     "90744","90745","90746","90748"],
}

ALL_ZIP_CODES = [zc for zcs in SOCAL_ZIP_CODES.values() for zc in zcs]

# Pipeline
class DataPipeline:
    def __init__(self, database_url: str):
        self.engine = get_engine(database_url)
        self.scrapers = {
            'zillow': ZillowScraper(),
            'redfin': RedfinScraper(),
            'la_assessor': LACountyAssessorScraper(),
        }
        self.feature_engineer = FeatureEngineer()

    # Public Entry Points
    def run_full_sync(self, zip_codes: list[str] = None):
        """Scrape everything. Run weekly."""
        zips = zip_codes or ALL_ZIP_CODES
        logger.info(f'[Pipeline] Full sync started - {len(zips)} zip codes')
        for source_name, scraper in self.scrapers.items():
            self._run_scraper(source_name, scraper, zips)

    def run_incremental(self, zip_codes: list[str] = None):
        """Only scrape recently updated listings. Run daily."""
        zips = zip_codes or ALL_ZIP_CODES
        logger.info(f'[Pipeline] Incremental sync - {len(zips)} zip codes')
        self._run_scraper('zillow', self.scrapers['zillow'], zips)

    def run_single_property(self, address: str, zip_code: str):
        """On-demand: scrape one specific property."""
        logger.info(f'[Pipeline] Single property: {address}, {zip_code}')
        # Attempt Zillow Detail Fetch
        zillow = self.scrapers['zillow']
        listings, stats = zillow.run([zip_code])
        matched = [l for l in listings if address.lower() in l.get('address', '').lower()]
        if matched:
            with get_session(self.engine) as session:
                self._upsert_property(session, matched[0])
                session.commit()

    # Core Scraper Runner
    def _run_scraper(self, source_name: str, scraper, zip_codes: list[str]):
        job = ScrapeJob(source=source_name, job_type='full_sync', status='running')

        with get_session(self.engine) as session:
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        try:
            listings, stats = scraper.run(zip_codes)
            saved = self._save_listings(listings)

            with get_session(self.engine) as session:
                j = session.get(ScrapeJob, job_id)
                j.status = 'success'
                j.records_fetched = stats['fetched']
                j.records_saved = saved['created']
                j.records_updated = saved['updated']
                j.records_skipped = saved['skipped']
                j.completed_at = datetime.utcnow()
                j.duration_secs = stats['duration_secs']
                session.commit()

            logger.info(f"[Pipeline] {source_name} - saved={saved['created']} updated={saved['updated']}")

        except Exception as e:
            logger.error(f'[Pipeline] {source_name} failed: {e}')
            with get_session(self.engine) as session:
                j = session.get(ScrapeJob, job_id)
                j.status = 'failed'
                j.error_log = str(e)
                j.completed_at = datetime.utcnow()
                session.commit()

    # Save Listings
    def _save_listings(self, listings: list[dict]) -> dict:
        stats = {
            'created': 0,
            'updated': 0,
            'skipped': 0,
        }

        with get_session(self.engine) as session:
            for item in listings:
                try:
                    result = self._upsert_property(session, item)
                    stats[result] += 1
                except Exception as e:
                    stats['skipped'] += 1
                    logger.warning(f'[Pipeline] Upsert error: {e}')

            session.commit()

        return stats

    def _upsert_property(self, session: Session, data: dict) -> str:
        """
        Insert or update a property record.
        Deduplication key: (address, zip_code) or parcel_number.
        """
        # Deduplicate by parcel number first, then by address+zip
        prop = None
        if data.get('parcel_number'):
            prop = session.execute(
                select(Property).where(Property.parcel_number == data['parcel_number'])
            ).scalar_one_or_none()

        if not prop:
            addr = self._normalize_address(data.get('address', ''))
            prop = session.execute(
                select(Property).where(
                    Property.address == addr,
                    Property.zip_code == str(data.get('zip_code', ''))
                )
            ).scalar_one_or_none()

        if prop:
            self._update_property(prop, data)
            return 'updated'
        else:
            prop = self._create_property(session, data)
            return 'created'

    def _create_property(self, session: Session, data: dict) -> Property:
        prop = Property(
            address = self._normalize_address(data.get('address', '')),
            city = data.get('city', ''),
            state = data.get('state', 'CA'),
            zip_code = str(data.get('zip_code', '')),
            latitude = data.get('latitude'),
            longitude = data.get('longitude'),
            parcel_number = data.get('parcel_number'),
            property_type = self._map_type(data.get('property_type')),
            lot_size_sqft = data.get('lot_size_sqft'),
            building_sqft = data.get('building_sqft'),
            year_built = data.get('year_built'),
            bedrooms = data.get('bedrooms'),
            bathrooms = data.get('bathrooms'),
            units = data.get('units', 1),
            estimated_value = data.get('estimated_value'),
            last_sale_price = data.get('last_sale_price'),
            last_sale_date = self._parse_date(data.get('last_sale_date')),
            assessed_value = data.get('assessed_value'),
            price_per_sqft = data.get('price_per_sqft'),
            data_source = data.get('source'),
            source_url = data.get('source_url', ''),
            raw_data = data.get('raw_data'),
        )
        session.add(prop)
        session.flush() # Get ID without committing

        # Add initial price history entry
        if prop.estimated_value:
            session.add(PriceHistory(
                property_id = prop.id,
                event_type = 'estimate',
                price = prop.estimated_value,
                price_sqft = prop.price_per_sqft,
                date = datetime.utcnow(),
                source = data.get('source'),
            ))

        return prop

    def _update_property(self, prop: Property, data: dict):
        """Update mutable fields; preserve existing data where new data is None."""
        fields = [
            "city", "state", "latitude", "longitude", "lot_size_sqft",
            "building_sqft", "year_built", "bedrooms", "bathrooms",
            "price_per_sqft", "source_url",
        ]
        for f in fields:
            if data.get(f) is not None:
                setattr(prop, f, data[f])

        # Always update estimated value (lasted wins)
        if data.get('estimated_value'):
            prop.estimated_value = data['estimated_value']

        prop.updated_at = datetime.utcnow()

    # Helpers
    @staticmethod
    def _normalize_address(addr: str) -> str:
        """Uppercase, strip extra spaces - basic normalization."""
        return " ".join(addr.upper().split()) if addr else ""

    @staticmethod
    def _parse_date(val) -> Optional[datetime]:
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d', '%m-%d-%Y'):
            try:
                return datetime.strptime(str(val), fmt)
            except ValueError:
                continue

        return None

    @staticmethod
    def _map_type(type_str: Optional[str]) -> PropertyType:
        mapping = {
            'single_family': PropertyType.SINGLE_FAMILY,
            'multi_family': PropertyType.MULTI_FAMILY,
            'condo': PropertyType.CONDO,
            'townhouse': PropertyType.TOWNHOUSE,
            'commercial': PropertyType.COMMERCIAL,
            'mixed_use': PropertyType.MIXED_USE,
            'vacant_land': PropertyType.VACANT_LAND,
        }
        return mapping.get(type_str or "", PropertyType.SINGLE_FAMILY)



