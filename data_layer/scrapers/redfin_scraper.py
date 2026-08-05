import csv
import json
import logging
from data_layer.scrapers.base_scraper import BaseScraper, ScraperConfig

logger = logging.getLogger('propiq.scrapers.redfin')

class RedfinScraper(BaseScraper):
    """
    Redfin exposes a stingray CSV API used by their own website.
    This is stable and commonly used for real estate datasets.
    """

    def __init__(self):
        config = ScraperConfig(
            source_name='redfin',
            base_url='https://www.redfin.com',  # no trailing slash - avoids the // bug
            requests_per_minute=5, # be polite with undocumented API
        )
        super().__init__(config)
        self._region_cache: dict[str, tuple[str, str] | None] = {}  # zip -> (region_id, region_type)

    # Region lookup
    def _resolve_region(self, zip_code: str) -> tuple[str, str] | None:
        """
        Redfin's CSV endpoint needs an internal region_id + region_type,
        not a zip code directly. Resolve it via the same autocomplete
        endpoint redfin.com's own search box uses. Cached per scraper
        instance since the same zip is looked up on every call otherwise.
        """
        if zip_code in self._region_cache:
            return self._region_cache[zip_code]

        resp = self.get(
            f'{self.config.base_url}/stingray/do/location-autocomplete',
            params={'location': zip_code, 'start': 0, 'count': 10, 'v': 2}
        )
        if not resp:
            self._region_cache[zip_code] = None
            return None

        try:
            # Same "{}&&<json>" prefix quirk as the CSV endpoint.
            raw = resp.text
            payload = json.loads(raw.split('&&', 1)[1] if '&&' in raw else raw)
            rows = [
                row
                for section in payload.get('payload', {}).get('sections', [])
                for row in section.get('rows', [])
            ]
            # Prefer an exact zip-code-type match; Redfin's zip rows carry
            # the literal zip in 'name' and an id shaped '<region_type>_<region_id>'.
            match = next((r for r in rows if r.get('name', '').strip() == zip_code), None) \
                    or (rows[0] if rows else None)
            if not match or '_' not in str(match.get('id', '')):
                logger.warning(f'[redfin] no region match for zip {zip_code} - response shape: {list(payload.keys())}')
                self._region_cache[zip_code] = None
                return None

            region_type, region_id = str(match['id']).split('_', 1)
            self._region_cache[zip_code] = (region_id, region_type)
            return region_id, region_type

        except Exception as e:
            logger.warning(f'[redfin] region lookup failed for zip {zip_code}: {e}')
            self._region_cache[zip_code] = None
            return None

    # Fetch
    def fetch_listings(self, zip_codes: list[str]) -> list[dict]:
        all_listings = []
        for zc in zip_codes:
            rows = self._fetch_zip(zc)
            all_listings.extend(rows)
            logger.info(f'[redfin] zip={zc} -> {len(rows)} listings')
        return all_listings

    def _fetch_zip(self, zip_code: str) -> list[dict]:
        region = self._resolve_region(zip_code)
        if region is None:
            logger.warning(f'[redfin] skipping zip {zip_code} - could not resolve region_id')
            return []
        region_id, region_type = region

        resp = self.get(
            f'{self.config.base_url}/stingray/api/gis-csv',
            params={
                'al': 1,
                'market': 'losangeles',
                'num_homes': 350,
                'region_id': region_id,
                'region_type': region_type,
                'sold_within_days': 365,
                'status': 9,
                'uipt': '1,2,3,4,6', # SF, condo, TH, MF, land - no spaces, Redfin rejects them
                'v': 8,
            }
        )
        if not resp:
            return []

        content = resp.text
        lines = content.split('\n')
        # First real header line starts with "SALE TYPE" (Redfin's actual
        # first CSV column) - previous check for a line starting with
        # 'ADDRESS' never matched anything, silently defaulting to line 1
        # (the disclaimer line) and feeding that to DictReader as headers.
        csv_start = next((i for i, l in enumerate(lines) if l.startswith('SALE TYPE')), None)
        if csv_start is None:
            logger.warning(f'[redfin] zip={zip_code} - no CSV header row found in response')
            return []
        reader = csv.DictReader(lines[csv_start:])
        return list(reader)

    # Parse
    def parse_listing(self, raw: dict) -> dict:
        def safe_float(v):
            try:
                return float(str(v).replace(',', '').replace('$', '')) if v else None
            except Exception:
                return None

        def safe_int(v):
            try:
                return int(str(v).replace(',', '')) if v else None
            except Exception:
                return None

        return {
            'source': 'redfin',
            'source_id': raw.get('MLS#', ""),
            'source_url': raw.get('URL', ''),

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
            'last_sale_date': raw.get('SOLD DATE'),  # was 'SOLd DATE' - typo, wrong case

            'raw_data': dict(raw),
        }

    def _map_property_type(self, rf_type: str) -> str:
        mapping = {
            'Single Family Residential': 'single_family',
            'Condo/Co-op': 'condo',
            'Townhouse': 'townhouse',
            'Multi-Family (2-4 Unit)': 'multi_family',
            'Multi-Family (5+ Unit)': 'multi_family',
            'Vacant Land': 'vacant_land',  # was 'Vacat Land' - typo, never matched
            'Commercial': 'commercial',
        }
        return mapping.get(rf_type, 'single_family')

    def to_property_dict(self, parsed: dict) -> dict:
        return parsed

# LA COUNTY ASSESSOR SCRAPER
# Public Records - No Auth Needed. Great For Parcel/Zoning Data.
class LACountyAssessorScraper(BaseScraper):
    """
    Fetches parcel data from LA County's public ArcGIS MapServer layer.
    Provides: AIN/APN, assessed value (land+improvement), use type, sqft,
    year built, bed/bath counts.

    Endpoint verified live Aug 2026:
    https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0
    Owner name/mailing address are NOT exposed on this layer per CA Gov
    Code §7928.205 - use SitusFullAddress only, no PII risk here.

    Prior version of this class pointed at a fabricated endpoint
    (assessor.lacounty.gov/api/assessor/search) that never existed -
    rewritten against the real, verified ArcGIS layer instead.
    """

    def __init__(self):
        config = ScraperConfig(
            source_name='la_county_assessor',
            base_url='https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0',
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
        all_features = []
        offset = 0
        page_size = 500
        while True:
            resp = self.get(
                f'{self.config.base_url}/query',
                params={
                    'where': f"SitusZIP='{zip_code}'",
                    'outFields': '*',
                    'f': 'json',
                    'resultOffset': offset,
                    'resultRecordCount': page_size,
                }
            )
            if not resp:
                break
            try:
                data = resp.json()
            except Exception as e:
                logger.warning(f'[la_assessor] zip={zip_code} - failed to parse JSON: {e}')
                break

            features = data.get('features', [])
            all_features.extend(features)

            if len(features) < page_size:
                break
            offset += page_size

        return all_features

    def parse_listing(self, raw: dict) -> dict:
        props = raw.get('attributes', {}) or {}

        city = (props.get('SitusCity') or '').strip()
        if city.upper().endswith(' CA'):
            city = city[:-3].strip()

        return {
            'source': 'la_county_assessor',
            'source_id': props.get('AIN', ''),
            'source_url': f"https://portal.assessor.lacounty.gov/parceldetail/{props.get('AIN', '')}",

            'address': props.get('SitusFullAddress', ''),
            'city': city,
            'state': 'CA',
            'zip_code': props.get('SitusZIP', ''),
            'latitude': props.get('CENTER_LAT'),
            'longitude': props.get('CENTER_LON'),

            'property_type': self._map_use_code(props.get('UseCode', '')),
            'zoning': None,
            'lot_size_sqft': props.get('Shape.STArea()'),
            'building_sqft': props.get('SQFTmain1'),
            'year_built': props.get('YearBuilt1'),
            'bedrooms': props.get('Bedrooms1'),
            'bathrooms': props.get('Bathrooms1'),
            'units': props.get('Units1', 1),

            'assessed_value': (props.get('Roll_LandValue') or 0) + (props.get('Roll_ImpValue') or 0),
            'last_sale_price': None,
            'last_sale_date': None,

            'raw_data': props,
        }

    def _map_use_code(self, use_code: str) -> str:
        residential = {'0100', '0101', '0102', '0103', '0104'}
        multi = {'0200', '0201', '0202', '0203', '0204'}
        commercial = {'1000', '1001', '1100', '1101'}
        if use_code in residential:
            return 'single_family'
        if use_code in multi:
            return 'multi_family'
        if use_code in commercial:
            return 'commercial'
        if str(use_code).startswith('8'):
            return 'vacant_land'
        return 'single_family'

    def to_property_dict(self, parsed: dict) -> dict:
        return parsed