"""
    PropIQ - Redfin Scraper
    Uses Redfin's unofficial CSV download endpoint (no API key needed).
    Works well for bulk SoCal data.

    @author Minh Thang Nguyen
    @version June 20. 2026
"""

import csv
import logging
from data_layer.scrapers.base_scraper import BaseScraper, ScraperConfig

logger = logging.getLogger('propiq.scrapers.redfin')

class RedfinScraper(BaseScraper):
    """
    Redfin exposes a stingray CSV API used by their own website.
    This is stable and commonly used for real estate datasets.
    """

    REDFIN_REGION_IDS = {
        # SoCal markets -> Redfin region IDs
        'Los Angeles County': '1',
        'Orange County': '2',
        'San Diego County': '3',
        'Riverside County': '4',
        'San Bernardino County': '5',
    }

    def __init__(self):
        config = ScraperConfig(
            source_name='redfin',
            base_url='https://www.redfin.com/',
            requests_per_minute=5, # be polite with undocumented API
        )
        super().__init__(config)

    # Fetch
    def fetch_listings(self, zip_codes: list[str]) -> list[dict]:
        all_listings = []
        for zc in zip_codes:
            rows = self._fetch_zip(zc)
            all_listings.extend(rows)
            logger.info(f'[redfin] zip={zc} -> {len(rows)} listings')
        return all_listings

    def _fetch_zip(self, zip_code: str) -> list[dict]:
        resp = self.get(
            f'{self.config.base_url}/stingray/api/gis-csv',
            params={
                'al': 1,
                'market': 'losangeles',
                'num_homes': 350,
                'region_id': zip_code,
                'region_type': 2,
                'sold_within_days': 365,
                'status': 9,
                'uipt': '1, 2, 3, 4, 6', # SF, condo, TH, MF, land
                'v': 8,
            }
        )
        if not resp:
            return []

        # Redfin returns CSV with a 1-line disclaimer header
        content = resp.text
        lines = content.split('\n')
        # Skip the 'REMARKS: ...' header line
        csv_start = next((i for i,l in enumerate(lines) if l.startswith('ADDRESS')), 1)
        reader = csv.DictReader(lines[csv_start:])
        return list(reader)

    # Parse
    def parse_listing(self, raw: dict) -> dict:
        def safe_float(v):
            try:
                return float(str(v)
                             .replace(',' , '')
                             .replace('$', '')) if v else None
            except:
                return None

        def safe_int(v):
            try:
                return int(str(v).replace(',' , '')) if v else None
            except:
                return None

        return {
            'source': 'redfin',
            'source_id': raw.get('MLS#', ""),
            'source_url': raw.get(
                'URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)',
                ''),

            'address': raw.get('ADDRESS', ''),
            'city': raw.get('CITY', ''),
            'state': raw.get('STATE OR PROVINCE', 'CA'),
            'zip_code': raw.get('ZIP OR POSTAL CODE', ''),
            'latitude': safe_float(raw.get('LATITUDE')),
            'longitude': safe_float(raw.get('LONGITUDE')),

            'property_type': self._map_property_type(raw.get('PROPERTY TYPE', '')),
            'bedrooms': safe_int(raw.get('BEDS')),
            'bathrooms': safe_float(raw.get('BATHS')),
            'building_sqft': safe_float(raw.get('SQUARE FEET')),
            'lot_size_sqft': safe_float(raw.get('LOT SIZE')),
            'year_built': safe_int(raw.get('YEAR BUILT')),

            'estimated_value': safe_float(raw.get('PRICE')),
            'price_per_sqft': safe_float(raw.get('$/SQUARE FEET')),
            'last_sale_price': safe_float(raw.get('SOLD PRICE')),
            'last_sale_date': raw.get('SOLd DATE'),

            'raw_data': dict(raw),
        }

    def _map_property_type(self, rf_type: str) -> str:
        mapping = {
            'Single Family Residential': 'single_family',
            'Condo/Co-op': 'condo',
            'Townhouse': 'townhouse',
            'Multi-Family (2-4 Unit)': 'multi_family',
            'Multi-Family (5+ Unit)': 'multi_family',
            'Vacat Land': 'vacant_land',
            'Commercial': 'commercial',
        }
        return mapping.get(rf_type, 'single_family')

# LA COUNTY ASSESSOR SCRAPER
# Public Records - No Auth Needed. Great For Parcel/Zoning Data.
class LACountyAssessorScraper(BaseScraper):
    """
    Fetches parcel data from the LA County Assessor's public portal.
    Provides: APN, assessed value, zoning, lot size, year built.
    Endpoint: https://www.lacounty.gov/api
    """

    def __init__(self):
        config = ScraperConfig(
            source_name='la_county_assessor',
            base_url='https://assessor.lacounty.gov',
            requests_per_minute=30,
        )
        super().__init__(config)

    def fetch_listings(self, zip_codes: list[str]) -> list[dict]:
        all_records = []
        for zc in zip_codes:
            records = self._search_by_zip(zc)
            all_records.extend(records)
            logger.info(f'[la_assessor] zip={zc} -> {len(records)} parcels')
        return all_records

    def _search_by_zip(self, zip_code: str) -> list[dict]:
        resp = self.get(
            f'{self.config.base_url}/api/assessor/search',
            params={
                'zipcode': zip_code,
                'count': 500,
                'start': 0,
            }
        )
        if not resp:
            return []
        data = resp.json()
        return data.get('features', [])

    def parse_listing(self, raw: dict) -> dict:
        props = raw.get('attributes', {})
        geo = raw.get('geometry', {}).get('rings', [[[]]])[0][0]

        return {
            'source': 'la_county_assessor',
            'source_id': props.get('APN', ''),
            'source_url': props.get('APN', ''),

            'address': props.get('SitusAddress', ''),
            'city': props.get('SitusCity', ''),
            'state': 'CA',
            'zip_code': str(props.get('SitusZop5', '')),
            'latitude': geo[1] if geo else None,
            'longitude': geo[2] if geo else None,

            'property_type': self._map_use_code(props.get('UseType', '')),
            'zoning': props.get('ZoneCode'),
            'lot_size_sqft': props.get('LotSizeSqFt'),
            'building_sqft': props.get('ImprovementSqFt'),
            'year_built': props.get('EffectiveYearBuilt'),
            'bedrooms': props.get('Bedrooms'),
            'bathrooms': props.get('Bathrooms'),
            'units': props.get('Units', 1),

            'assessed_value': props.get('NetTaxableValue'),
            'last_sale_price': props.get('LastSaleAmount'),
            'last_sale_date': props.get('LastSaleDate'),

            'raw_data': props,
        }

    def _map_use_code(self, code: str) -> str:
        # LA County use codes -> PropIQ types
        residential = {'0100', '0101', '0103', '0104'}
        multi = {'0200', '0201', '0204'}
        commercial = {'1000', '1001', '1100', '1101'}
        if code in residential: return 'single_family'
        if code in multi: return 'multi_family'
        if code in commercial: return 'commercial'
        if code.startswith('8'): return 'vacant_land'
        return 'single_family'
