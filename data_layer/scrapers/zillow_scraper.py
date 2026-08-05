"""
    PropIQ - Zillow Scraper
    Uses the Zillow API via RapidAPI (zillow-com1.p.rapidapi.com).
    Free tier: 50 req/month -> upgrade for production
    Alternatively swap in the zillow-scraper Python library for local use.

    @author Minh Thang Nguyen
    @version June 20, 2026
"""

import os
import logging
from data_layer.scrapers.base_scraper import BaseScraper, ScraperConfig

logger = logging.getLogger('propiq.scrapers.zillow')

class ZillowScraper(BaseScraper):
    """
    Uses ZLLW Working API (RapidAPI) - NOT the old 'zillow-com1' API by
    apimaker, which was delisted from the marketplace as of Aug 2026.
    Schema + required params (location, listingStatus) confirmed against
    a live /search/byaddress request/response - see PropIQ session notes,
    Aug 2026. Free tier: 500 req/month - keep max_pages low during testing.
    """

    def __init__(self, max_pages: int = 1):
        config = ScraperConfig(
            source_name='zillow',
            base_url='https://zllw-working-api.p.rapidapi.com',
            requests_per_minute=10,
            extra_headers={
                'x-rapidapi-host': 'zllw-working-api.p.rapidapi.com',
                'x-rapidapi-key': os.environ['RAPIDAPI_KEY'],
            },
        )
        super().__init__(config)
        self.max_pages = max_pages  # API allows up to 5 (1000 results); default 1 to conserve quota

    def fetch_listings(self, zip_codes: list[str]) -> list[dict]:
        all_listings = []
        for zc in zip_codes:
            rows = self._fetch_zip(zc)
            all_listings.extend(rows)
            logger.info(f'[zillow] zip={zc} -> {len(rows)} listings')
        return all_listings

    def _fetch_zip(self, zip_code: str) -> list[dict]:
        all_rows = []
        for page in range(1, self.max_pages + 1):
            resp = self.get(
                f'{self.config.base_url}/search/byaddress',
                params={
                    'location': zip_code,
                    'listingStatus': 'For_Sale',
                    'page': page,
                },
            )
            if not resp:
                break
            try:
                data = resp.json()
            except Exception as e:
                logger.warning(f'[zillow] zip={zip_code} page={page} - failed to parse JSON: {e}')
                break

            results = data.get('searchResults', [])
            rows = [r['property'] for r in results if 'property' in r]
            all_rows.extend(rows)

            total_pages = data.get('pagesInfo', {}).get('totalPages', 1)
            if page >= total_pages or not rows:
                break

        return all_rows

    def parse_listing(self, raw: dict) -> dict:
        addr = raw.get('address', {}) or {}
        loc = raw.get('location', {}) or {}
        price = raw.get('price', {}) or {}
        lot = raw.get('lotSizeWithUnit', {}) or {}
        listing = raw.get('listing', {}) or {}
        estimates = raw.get('estimates', {}) or {}
        tax = raw.get('taxAssessment', {}) or {}

        lot_sqft = lot.get('lotSize')
        if lot.get('lotSizeUnit') == 'acres' and lot_sqft is not None:
            lot_sqft = lot_sqft * 43560

        return {
            'source': 'zillow',
            'source_id': str(raw.get('zpid', '')),
            'source_url': f"https://www.zillow.com{raw.get('hdpView', {}).get('hdpUrl', '')}",

            'address': addr.get('streetAddress', ''),
            'city': addr.get('city', ''),
            'state': addr.get('state', 'CA'),
            'zip_code': addr.get('zipcode', ''),
            'latitude': loc.get('latitude'),
            'longitude': loc.get('longitude'),

            'property_type': self._map_property_type(raw.get('propertyType', '')),
            'bedrooms': raw.get('bedrooms'),
            'bathrooms': raw.get('bathrooms'),
            'building_sqft': raw.get('livingArea'),
            'lot_size_sqft': lot_sqft,
            'year_built': raw.get('yearBuilt'),

            'estimated_value': price.get('value') or estimates.get('zestimate'),
            'price_per_sqft': price.get('pricePerSquareFoot'),
            'last_sale_price': None,
            'last_sale_date': None,

            'listing_status': listing.get('listingStatus'),
            'tax_assessed_value': tax.get('taxAssessedValue'),

            'raw_data': raw,
        }

    def _map_property_type(self, zillow_type: str) -> str:
        mapping = {
            'singleFamily': 'single_family',
            'condo': 'condo',
            'townhouse': 'townhouse',
            'multiFamily': 'multi_family',
            'land': 'vacant_land',
            'apartment': 'multi_family',
        }
        return mapping.get(zillow_type, 'single_family')

    def to_property_dict(self, parsed: dict) -> dict:
        return parsed