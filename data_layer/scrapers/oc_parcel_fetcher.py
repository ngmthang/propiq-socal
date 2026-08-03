"""
    PropIQ - Orange County Parcel Fetcher
    Pulls real parcel attributes (APN, site address, year built, bedroom
    count) from OC Public Works' public ArcGIS parcel layer:
        https://www.ocgis.com/arcpub/rest/services/Map_Layers/Parcels/MapServer/0
    No API key, no auth - this is the same public REST endpoint OC's own
    web map viewers hit.

    That layer does NOT carry a zip/city field, so we join it against the
    US Census Bureau's public TIGERweb ZCTA layer to get each zip code's
    real boundary polygon, then keep only parcels whose centroid actually
    falls inside that polygon (not just inside its bounding box).

    NOTE: CA county assessors do not publicly disclose real sale prices,
    so this fetcher never populates estimated_value / last_sale_price -
    those stay synthetic, generated elsewhere in the pipeline. This file
    only supplies real physical/location attributes.

    @author Minh Thang Nguyen
    @version August 1, 2026
"""

import json
import time
import random
import logging
from typing import Optional
from data_layer.scrapers.base_scraper import BaseScraper, ScraperConfig

logger = logging.getLogger('propiq.scrapers.oc_parcels')

OC_PARCELS_BASE_URL = 'https://www.ocgis.com/arcpub/rest/services/Map_Layers/Parcels/MapServer/0'
CENSUS_ZCTA_URL = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/2/query'

# OC Development Services zoning layer (public, key-free). IMPORTANT
# caveat: this covers UNINCORPORATED county land - cities (Irvine, Anaheim,
# etc.) run their own zoning, so parcels inside city limits usually won't
# intersect any polygon here and correctly fall back to UNKNOWN.
OC_ZONING_URL = 'https://www.ocgis.com/survey/rest/services/WebApps/Map_Layers_Associated_Documents/FeatureServer/9/query'

# Real OC ZONECLASS codes -> PropIQ ZoningType, mapped BY MEANING. Never
# map by string value: our enum's internal values collide with real codes
# (e.g. real-world 'M1' = Light Industrial, but ZoningType.MIXED_USE='M1').
# PC (Planned Community), S (Specific Plan), SG: deliberately unmapped -
# the code alone doesn't reveal actual use, so they stay UNKNOWN rather
# than a guess.
OC_ZONECLASS_TO_ZONING = {
    'R1': 'RESIDENTIAL_LOW', 'RS': 'RESIDENTIAL_LOW', 'RE': 'RESIDENTIAL_LOW',
    'RHE': 'RESIDENTIAL_LOW', 'E1': 'RESIDENTIAL_LOW', 'E4': 'RESIDENTIAL_LOW',
    'R2D': 'RESIDENTIAL_LOW',
    'R2': 'RESIDENTIAL_MEDIUM', 'R4': 'RESIDENTIAL_MEDIUM',
    'R3': 'RESIDENTIAL_HIGH',
    'C1': 'COMMERCIAL', 'CN': 'COMMERCIAL',
    'C2': 'COMMERCIAL_GENERAL', 'CC': 'COMMERCIAL_GENERAL', 'CH': 'COMMERCIAL_GENERAL',
    'PA': 'COMMERCIAL', 'RP': 'COMMERCIAL',
    'M1': 'INDUSTRIAL_LIGHT',
    'A1': 'AGRICULTURAL', 'AR': 'AGRICULTURAL',
    'OS': 'OPEN_SPACE', 'B1': 'OPEN_SPACE', 'R/OSP': 'OPEN_SPACE',
}

# This is the FULL field list OC's public parcel layer exposes. Owner name,
# building sqft, lot size, bathrooms, and zoning are NOT on this layer.
OC_PARCEL_FIELDS = ['OBJECTID', 'SITE_ADDRESS', 'ASSESSMENT_NO', 'YEAR_BUILT', 'NBR_BEDROOMS']

OC_SERVER_MAX_RECORDS = 1000  # layer's MaxRecordCount
PAGINATION_GUARD = 50_000     # hard stop so a bad bbox can't loop forever

# Curated OC zip -> city map. ZCTAs don't carry a city name, so we keep our
# own lookup for the zips we track. Extend this as coverage grows - it
# mirrors (and expands on) the "Irvine" entry already in
# pipeline.SOCAL_ZIP_CODES.
OC_ZIP_CITY = {
    '92602': 'Irvine', '92603': 'Irvine', '92604': 'Irvine', '92606': 'Irvine',
    '92612': 'Irvine', '92614': 'Irvine', '92617': 'Irvine', '92618': 'Irvine',
    '92620': 'Irvine', '92697': 'Irvine',
    '92626': 'Costa Mesa', '92627': 'Costa Mesa',
    '92646': 'Huntington Beach', '92647': 'Huntington Beach',
    '92648': 'Huntington Beach', '92649': 'Huntington Beach',
    '92660': 'Newport Beach', '92661': 'Newport Beach', '92663': 'Newport Beach',
    '92651': 'Laguna Beach',
    '92672': 'San Clemente',
    '92675': 'San Juan Capistrano',
    '92688': 'Rancho Santa Margarita',
    '92691': 'Mission Viejo', '92692': 'Mission Viejo',
    '92701': 'Santa Ana', '92703': 'Santa Ana', '92704': 'Santa Ana',
    '92705': 'Santa Ana', '92706': 'Santa Ana', '92707': 'Santa Ana',
    '92780': 'Tustin', '92782': 'Tustin',
    '92801': 'Anaheim', '92802': 'Anaheim', '92804': 'Anaheim',
    '92805': 'Anaheim', '92806': 'Anaheim', '92807': 'Anaheim', '92808': 'Anaheim',
    '92821': 'Brea',
    '92831': 'Fullerton', '92832': 'Fullerton', '92833': 'Fullerton', '92835': 'Fullerton',
    '92840': 'Garden Grove', '92841': 'Garden Grove', '92843': 'Garden Grove',
    '92844': 'Garden Grove', '92845': 'Garden Grove',
    '92865': 'Orange', '92866': 'Orange', '92867': 'Orange',
    '92868': 'Orange', '92869': 'Orange',
    '92870': 'Placentia',
    '92886': 'Yorba Linda',
}


class OcParcelFetcher(BaseScraper):
    """
    Fetches real OC parcel records for a list of zip codes.
    Geometry/zip join is done client-side against Census ZCTA polygons -
    see module docstring.
    """

    def __init__(self):
        config = ScraperConfig(
            source_name='oc_parcel_gis',
            base_url=OC_PARCELS_BASE_URL,
            requests_per_minute=30,  # public server, but let's still be polite
            timeout=60,  # esriSpatialRelIntersects against a complex ZCTA polygon is slow server-side
        )
        super().__init__(config)

    # Fetch
    def fetch_listings(self, zip_codes: list[str]) -> list[dict]:
        all_parcels = []
        for zip_code in zip_codes:
            city = OC_ZIP_CITY.get(zip_code)
            if not city:
                logger.warning(
                    f'[oc_parcels] zip={zip_code} not in OC_ZIP_CITY - add it there first, skipping'
                )
                continue

            zcta = self._fetch_zcta_boundary(zip_code)
            if not zcta:
                logger.warning(f'[oc_parcels] zip={zip_code} - no ZCTA boundary from Census, skipping')
                continue

            boundary_rings = self._fetch_zcta_boundary(zip_code)
            if not boundary_rings:
                logger.warning(f'[oc_parcels] zip={zip_code} - no ZCTA boundary from Census, skipping')
                continue

            candidates = self._fetch_parcels_in_geometry(boundary_rings)
            logger.info(f'[oc_parcels] zip={zip_code} ({city}) - {len(candidates)} candidates intersecting polygon')

            # Real zoning polygons overlapping this zip (unincorporated
            # areas only - see OC_ZONING_URL note). Empty list is normal
            # for parcels inside city limits.
            zoning_polys = self._fetch_zoning_polygons(boundary_rings)
            if zoning_polys:
                logger.info(f'[oc_parcels] zip={zip_code} - {len(zoning_polys)} county zoning polygons overlap')

            kept = 0
            skipped_junk = 0
            for feature in candidates:
                geom = feature.get('geometry') or {}
                parcel_rings = geom.get('rings')
                if not parcel_rings:
                    continue

                cx, cy = self._polygon_centroid(parcel_rings)
                if cx is None:
                    continue
                if not self._point_in_polygon(cx, cy, boundary_rings):
                    continue  # centroid falls outside this zip's real boundary

                attrs = dict(feature.get('attributes', {}))

                apn = (attrs.get('ASSESSMENT_NO') or '').strip()
                if not apn or set(apn) <= set('0-'):
                    skipped_junk += 1
                    continue

                attrs['_zip_code'] = zip_code
                attrs['_city'] = city
                attrs['_latitude'] = cy
                attrs['_longitude'] = cx
                attrs['_zoneclass'] = self._zoning_for_point(cx, cy, zoning_polys)
                all_parcels.append(attrs)
                kept += 1

            logger.info(f'[oc_parcels] zip={zip_code} ({city}) - {kept} parcels matched after '
                        f'zip-boundary filter ({skipped_junk} junk/placeholder APNs skipped)')

        return all_parcels

    def fetch_by_apn(self, apn: str) -> Optional[dict]:
        """On-demand: look up a single parcel by its Assessment Number (APN)."""
        resp = self.get(
            f'{OC_PARCELS_BASE_URL}/query',
            params={
                'where': f"ASSESSMENT_NO='{apn}'",
                'outFields': ','.join(OC_PARCEL_FIELDS),
                'returnGeometry': 'true',
                'outSR': 4326,
                'f': 'json',
            },
        )
        if not resp:
            return None
        features = resp.json().get('features', [])
        return features[0] if features else None

    def _fetch_zcta_boundary(self, zip_code: str) -> Optional[list]:
        """Get a zip code's real boundary polygon rings from Census TIGERweb."""
        resp = self.get(
            CENSUS_ZCTA_URL,
            params={
                'where': f"ZCTA5='{zip_code}'",
                'outFields': 'ZCTA5',
                'returnGeometry': 'true',
                'outSR': 4326,
                'f': 'json',
            },
        )
        if not resp:
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.error(f'[oc_parcels] Census response for zip={zip_code} was not JSON')
            return None

        features = data.get('features', [])
        if not features:
            return None
        rings = features[0].get('geometry', {}).get('rings')
        return rings or None

    def _post_with_retries(self, url: str, data: dict):
        """
        POST version of BaseScraper.get(). ZCTA polygons can carry
        thousands of vertices - large enough that sending them as a GET
        query string risks exceeding URL length limits (some servers just
        drop the connection instead of returning a clean 414). ArcGIS's
        query operation accepts the same parameters as a POST body, so we
        use that here instead. Mirrors get()'s throttle/retry/backoff
        behavior rather than duplicating BaseScraper wholesale.
        """
        for attempt in range(self.config.max_retries):
            self._throttle()
            self._rotate_user_agent()
            try:
                resp = self.session.post(url, data=data, timeout=self.config.timeout)
                resp.raise_for_status()
                logger.debug(f'[{self.config.source_name}] POST {url} -> {resp.status_code}')
                return resp
            except Exception as e:
                wait = (2 ** attempt) * self.config.backoff_factor + random.uniform(0, 2)
                logger.warning(f'[{self.config.source_name}] POST failed ({e}), attempt {attempt + 1}')
                time.sleep(wait)

        logger.error(f'[{self.config.source_name}] All {self.config.max_retries} POST retries failed: {url}')
        return None

    def _fetch_parcels_in_geometry(self, rings: list) -> list[dict]:
        """Page through OC parcels intersecting a real polygon (a zip's
        ZCTA boundary), rather than its bounding box - the server does the
        shape filtering, so far fewer irrelevant parcels get transferred
        and then thrown away client-side."""
        geometry = json.dumps({'rings': rings, 'spatialReference': {'wkid': 4326}})
        features = []
        offset = 0

        while True:
            resp = self._post_with_retries(
                f'{OC_PARCELS_BASE_URL}/query',
                data={
                    'where': '1=1',
                    'geometry': geometry,
                    'geometryType': 'esriGeometryPolygon',
                    'inSR': 4326,
                    'spatialRel': 'esriSpatialRelIntersects',
                    'outFields': ','.join(OC_PARCEL_FIELDS),
                    'returnGeometry': 'true',
                    'outSR': 4326,
                    'orderByFields': 'OBJECTID',
                    'resultOffset': offset,
                    'resultRecordCount': OC_SERVER_MAX_RECORDS,
                    'f': 'json',
                },
            )
            if not resp:
                break
            try:
                data = resp.json()
            except ValueError:
                logger.error('[oc_parcels] OC parcel response was not JSON')
                break

            if 'error' in data:
                logger.error(f"[oc_parcels] ArcGIS error: {data['error']}")
                break

            page = data.get('features', [])
            features.extend(page)

            if len(page) < OC_SERVER_MAX_RECORDS and not data.get('exceededTransferLimit'):
                break

            offset += OC_SERVER_MAX_RECORDS
            if offset > PAGINATION_GUARD:
                logger.warning('[oc_parcels] pagination guard hit, stopping early')
                break

        return features

    def _fetch_zoning_polygons(self, boundary_rings: list) -> list[tuple]:
        """Fetch county zoning polygons intersecting a zip's boundary.
        Returns [(rings, zoneclass), ...]. Empty for fully-incorporated
        areas - that's expected, not an error."""
        geometry = json.dumps({'rings': boundary_rings, 'spatialReference': {'wkid': 4326}})
        resp = self._post_with_retries(
            OC_ZONING_URL,
            data={
                'where': '1=1',
                'geometry': geometry,
                'geometryType': 'esriGeometryPolygon',
                'inSR': 4326,
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'ZONECLASS',
                'returnGeometry': 'true',
                'outSR': 4326,
                'f': 'json',
            },
        )
        if not resp:
            return []
        try:
            data = resp.json()
        except ValueError:
            logger.error('[oc_parcels] zoning response was not JSON')
            return []
        if 'error' in data:
            logger.error(f"[oc_parcels] zoning ArcGIS error: {data['error']}")
            return []

        polys = []
        for feat in data.get('features', []):
            rings = (feat.get('geometry') or {}).get('rings')
            zc = (feat.get('attributes') or {}).get('ZONECLASS')
            if rings and zc:
                polys.append((rings, zc.strip()))
        return polys

    def _zoning_for_point(self, x: float, y: float, zoning_polys: list[tuple]) -> Optional[str]:
        """Return the ZONECLASS whose polygon contains the point, if any."""
        for rings, zoneclass in zoning_polys:
            if self._point_in_polygon(x, y, rings):
                return zoneclass
        return None

    @staticmethod
    def _polygon_centroid(rings: list) -> tuple:
        """Simple vertex-average centroid of the outer ring. Good enough for
        a small parcel footprint; not area-weighted."""
        if not rings or not rings[0]:
            return None, None
        outer = rings[0]
        xs = [pt[0] for pt in outer]
        ys = [pt[1] for pt in outer]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    @staticmethod
    def _point_in_polygon(x: float, y: float, rings: list) -> bool:
        """Ray-casting point-in-polygon, applied across every ring so holes
        (interior rings) correctly toggle the inside/outside state."""
        inside = False
        for ring in rings:
            n = len(ring)
            j = n - 1
            for i in range(n):
                xi, yi = ring[i][0], ring[i][1]
                xj, yj = ring[j][0], ring[j][1]
                intersects = ((yi > y) != (yj > y)) and (
                    x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi
                )
                if intersects:
                    inside = not inside
                j = i
        return inside

    # Parse
    def parse_listing(self, raw: dict) -> dict:
        apn = (raw.get('ASSESSMENT_NO') or '').strip()
        zoneclass = raw.get('_zoneclass')
        return {
            'source': 'oc_parcel_gis',
            'source_id': apn,
            'source_url': f'{OC_PARCELS_BASE_URL}/query',

            'address': (raw.get('SITE_ADDRESS') or '').strip(),
            'city': raw.get('_city', ''),
            'state': 'CA',
            'zip_code': raw.get('_zip_code', ''),
            'county': 'Orange',
            'latitude': raw.get('_latitude'),
            'longitude': raw.get('_longitude'),
            'parcel_number': apn,

            'year_built': self._safe_int(raw.get('YEAR_BUILT')),
            'bedrooms': self._safe_int(raw.get('NBR_BEDROOMS')),

            # Not exposed on OC's public layer - left null for the
            # assessor/ML pipeline (or a future zoning-layer join) to fill.
            'bathrooms': None,
            'building_sqft': None,
            'lot_size_sqft': None,
            'property_type': None,
            # Real county ZONECLASS via zoning-layer join, mapped by
            # meaning; None for city-zoned parcels (join covers
            # unincorporated land only) or unmapped codes (PC/S/SG).
            'zoning': OC_ZONECLASS_TO_ZONING.get(zoneclass) if zoneclass else None,
            'raw_zoneclass': zoneclass,

            # CA doesn't disclose real sale prices publicly - PropIQ keeps
            # these synthetic downstream. Never populated here.
            'estimated_value': None,
            'last_sale_price': None,
            'last_sale_date': None,

            'raw_data': raw,
        }

    def to_property_dict(self, parsed: dict) -> dict:
        return parsed

    @staticmethod
    def _safe_int(v) -> Optional[int]:
        try:
            return int(str(v).strip()) if v not in (None, '') else None
        except (ValueError, TypeError):
            return None