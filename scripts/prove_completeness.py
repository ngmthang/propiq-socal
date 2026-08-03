"""
    PropIQ - Completeness Proof (trimmed vs lost)
    The per-zip completeness ratio conflates boundary geometry with data
    loss. This settles which one is happening for a given zip: it fetches
    the county's ACTUAL parcel APNs inside that zip's polygon, then checks
    each one against our DB - not just "is it stored under this zip" but
    "is it stored AT ALL (under any zip)". A parcel the county lists that
    we stored under a NEIGHBORING zip = correctly trimmed by centroid
    filtering. One we didn't store anywhere = genuinely lost.

    Usage:
        python scripts/prove_completeness.py --zip 92602 --sample 200

    @author Minh Thang Nguyen
    @version August 3, 2026
"""

import os
import sys
import json
import random
import argparse
import logging

from sqlalchemy import create_engine, text

from data_layer.scrapers.oc_parcel_fetcher import (
    OcParcelFetcher, OC_PARCELS_BASE_URL, OC_SERVER_MAX_RECORDS,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.prove')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')


def _county_apns_in_polygon(fetcher: OcParcelFetcher, rings: list) -> set:
    """All valid (non-junk) APNs the county places inside a zip polygon."""
    geometry = json.dumps({'rings': rings, 'spatialReference': {'wkid': 4326}})
    apns, offset = set(), 0
    while True:
        resp = fetcher._post_with_retries(
            f'{OC_PARCELS_BASE_URL}/query',
            data={
                'where': '1=1',
                'geometry': geometry,
                'geometryType': 'esriGeometryPolygon',
                'inSR': 4326,
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'ASSESSMENT_NO',
                'returnGeometry': 'false',
                'resultOffset': offset,
                'resultRecordCount': OC_SERVER_MAX_RECORDS,
                'f': 'json',
            },
        )
        if not resp:
            break
        data = resp.json()
        page = data.get('features', [])
        for feat in page:
            apn = (feat.get('attributes', {}).get('ASSESSMENT_NO') or '').strip()
            if apn and not set(apn) <= set('0-'):
                apns.add(apn)
        if len(page) < OC_SERVER_MAX_RECORDS and not data.get('exceededTransferLimit'):
            break
        offset += OC_SERVER_MAX_RECORDS
        if offset > 60000:
            break
    return apns


def run(zip_code: str, sample: int, database_url: str) -> int:
    engine = create_engine(database_url)
    fetcher = OcParcelFetcher()

    rings = fetcher._fetch_zcta_boundary(zip_code)
    if not rings:
        logger.error(f'no ZCTA boundary for {zip_code}')
        return 1

    county_apns = _county_apns_in_polygon(fetcher, rings)
    logger.info(f'[prove] county lists {len(county_apns):,} valid parcels intersecting {zip_code}')

    with engine.connect() as conn:
        # what we stored UNDER this zip
        here = {r[0] for r in conn.execute(text(
            "SELECT parcel_number FROM properties WHERE data_source='oc_parcel_gis' AND zip_code=:z"
        ), {'z': zip_code}).all()}

    # the interesting set: county says in-polygon, but we did NOT store here
    not_here = list(county_apns - here)
    logger.info(f'[prove] {len(here):,} stored under {zip_code}; '
                f'{len(not_here):,} county-in-polygon parcels NOT stored under {zip_code}')

    if not not_here:
        logger.info('[prove] we stored every county parcel under this zip - nothing to explain')
        return 0

    # sample those and see where (if anywhere) we DID store them
    rng = random.Random(42)
    probe = rng.sample(not_here, min(sample, len(not_here)))
    with engine.connect() as conn:
        placement = dict(conn.execute(text("""
            SELECT parcel_number, zip_code FROM properties
            WHERE data_source='oc_parcel_gis' AND parcel_number = ANY(:apns)
        """), {'apns': probe}).all())

    trimmed = {a: z for a, z in placement.items()}          # stored under a neighbor
    lost = [a for a in probe if a not in placement]         # not stored anywhere

    logger.info(f'[prove] probed {len(probe)} not-here parcels: '
                f'{len(trimmed)} stored under a NEIGHBORING zip (correctly trimmed), '
                f'{len(lost)} not stored ANYWHERE (genuinely lost)')

    # show the neighbor distribution
    from collections import Counter
    neigh = Counter(trimmed.values())
    for z, n in neigh.most_common(8):
        logger.info(f'    -> {n:>4} landed in {z}')
    for a in lost[:10]:
        logger.warning(f'    LOST: {a} (county lists it in {zip_code}, not in our DB at all)')

    lost_rate = len(lost) / len(probe)
    logger.info(f'[prove] genuine loss rate in sample: {lost_rate:.1%}')
    return 1 if lost_rate > 0.02 else 0


def main():
    p = argparse.ArgumentParser(description='Prove whether low per-zip retention is trimming or loss.')
    p.add_argument('--zip', required=True)
    p.add_argument('--sample', type=int, default=200)
    p.add_argument('--database-url', default=DATABASE_URL)
    args = p.parse_args()
    sys.exit(run(args.zip, args.sample, args.database_url))


if __name__ == '__main__':
    main()