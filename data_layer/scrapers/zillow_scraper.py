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
from typing import Optional
from data_layer.scrapers.base_scraper import BaseScraper, ScraperConfig

logger = logging.getLogger('propiq.scrapers.zillow')

ZILLOW_API_HOST = 'zillow-com1.p.rapidapi.com'
ZILLOW_API_KEY = os.getenv('RAPIDAPI_KEY', '')

class ZillowScraper(BaseScraper):

    def __init__(self):
        config = ScraperConfig(
            source_name = 'zillow',
            base_url = f'https://{ZILLOW_API_HOST}',
            request_headers = 10, # RapidAPI free tier is slow
            extra_headers = {
                'X-RapidAPI-Key': ZILLOW_API_KEY,
                'X-RapidAPI-Host': ZILLOW_API_HOST,
            }
        )
        super().__init__(config)

    # Fetch
    def fetch_listings(self, zip_codes: list[str]) -> list[dict]:
        all_listings = []
        for zc in zip_codes:
            page = 1
            while True:
                data = self._fetch_page(zc, page)
                if not data:
                    break
                listings = data.get('props', [])
                all_listings.extend(listings)
                logger.info(f'[zillow] zip={zc} page={page} -> {len(listings)} listings')

                # Zillow paginates in 40-item pages
                if len(listings) < 40 or page >= 20:
                    break
                page += 1

        return all_listings

    def _fetch_page(self, zip_code: str, page: int) -> Optional[dict]:
        resp = self.get(
            f'{self.config.base_url}/propertyExtendedSearch',
            params = {
                'location': zip_code,
                'page': page,
                'status_type': 'ForSale',
                'home_type': 'Houses, Apartments, Condos, Townhomes, Multi-family',
                'sort': 'Newest',
            }
        )
        return resp.json() if resp else None

    def fetch_property_details(self, zpid: str) ->Optional[dict]:
        """Fetch full details for one property by Zillow ID."""
        resp = self.get(
            f'{self.config.base_url}/property',
            params = {'zpid': zpid}
        )
        return resp.json() if resp else None

    def fetch_zestimate_history(self, zpid: str) -> Optional[dict]:
        """Fetch Zestimate price history for a property."""
        resp = self.get(
            f'{self.config.base_url}/zpropertyHistory',
            params = {'zpid': zpid}
        )
        if resp:
            data = resp.json()
            return data.get('data', {}).get('zestimateHistory', [])
        return None

    # Parse
    def parse_listing(self, raw: dict) -> dict:
        """
        Normalize a Zillow listing to PropIQ's standard schema.
        """
        address = raw.get('address', {})
        price = raw.get('price')

        return {
            # Identity
            'source': 'zillow',
            'source_id': str(raw.get('zpid', '')),
            'source_url': raw.get('detailUrl', ''),

            # Location
            'address': address.get('streetAddress', ''),
            'city': address.get('city', ''),
            'state': address.get('state', 'CA'),
            'zip_code': str(address.get('zipCode', '')),
            'latitude': address.get('latitude', ''),
            'longitude': address.get('longitude', ''),

            # Physical
            'property_type': self._map_property_type(raw.get('propertyType', '')),
            'bedrooms': raw.get('bedrooms'),
            'bathrooms': raw.get('bathrooms'),
            'building_sqft': raw.get('livingArea'),
            'lot_size_sqft': raw.get('lotAreaValue'),
            'year_built': raw.get('yearBuilt'),

            # Valuation
            'estimated_value': price,
            'price_per_sqft': raw.get('pricePerSquareFoot'),
            'last_sale_price': raw.get('lastSoldPrice'),
            'last_sale_date': raw.get('lastSoldDate'),

            # Raw
            'raw_data': raw,
        }

    def _map_home_type(self, zillow_type: str) -> str:
        mapping = {
            'SINGLE_FAMILY': 'single_family',
            'CONDO': 'condo',
            'TOWNHOUSE': 'townhouse',
            'MULTI_FAMILY': 'multi_family',
            'APARTMENT': 'multi_apartment',
            'LOT': 'vacant_land',
            'MANUFACTURING': 'single_family',
        }
        return mapping.get(zillow_type.upper(), 'single_family')

