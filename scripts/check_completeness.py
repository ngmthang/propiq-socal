"""
    PropIQ - Ingestion Completeness Check
    For each tracked zip, asks the live county endpoint how many parcels
    fall in that zip's boundary polygon (returnCountOnly), and compares to
    how many we actually stored. Answers "did we get ALL the parcels?" -
    the completeness question that correctness checks (audit, sample-verify)
    can't answer.

    Expected: our count is a high, STABLE fraction of the county's polygon
    count - never equal. The county's esriSpatialRelIntersects count
    includes every parcel merely touching the ZCTA boundary; we keep only
    centroid-inside parcels and drop junk APNs, so ~80-95% retention per
    zip is healthy. A zip far below the overall ratio signals parcels were
    silently lost (pagination guard hit, a failed page, a boundary miss) -
    that's the real thing this catches.

    Usage:
        python scripts/check_completeness.py
        python scripts/check_completeness.py --zips 92602,92603

    @author Minh Thang Nguyen
    @version August 3, 2026
"""

import os
import sys
import json
import argparse
import logging

from sqlalchemy import create_engine, text

from data_layer.scrapers.oc_parcel_fetcher import (
    OcParcelFetcher, OC_ZIP_CITY, OC_PARCELS_BASE_URL,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.completeness')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')

# A zip retaining far less than the fleet-wide average is the alarm.
LOW_RETENTION_FLAG = 0.60


def _county_count_in_polygon(fetcher: OcParcelFetcher, rings: list) -> int:
    """Ask the county how many parcels intersect a zip's boundary polygon."""
    geometry = json.dumps({'rings': rings, 'spatialReference': {'wkid': 4326}})
    resp = fetcher._post_with_retries(
        f'{OC_PARCELS_BASE_URL}/query',
        data={
            'where': '1=1',
            'geometry': geometry,
            'geometryType': 'esriGeometryPolygon',
            'inSR': 4326,
            'spatialRel': 'esriSpatialRelIntersects',
            'returnCountOnly': 'true',
            'f': 'json',
        },
    )
    if not resp:
        return -1
    try:
        return int(resp.json().get('count', -1))
    except (ValueError, TypeError):
        return -1


def run(zips: list[str], database_url: str) -> int:
    engine = create_engine(database_url)
    fetcher = OcParcelFetcher()

    with engine.connect() as conn:
        stored = dict(conn.execute(text("""
            SELECT zip_code, COUNT(*) FROM properties
            WHERE data_source = 'oc_parcel_gis' AND zip_code = ANY(:zips)
            GROUP BY zip_code
        """), {'zips': zips}).all())

    rows, total_stored, total_county = [], 0, 0
    for zip_code in zips:
        rings = fetcher._fetch_zcta_boundary(zip_code)
        if not rings:
            logger.warning(f'[completeness] zip={zip_code} - no ZCTA boundary, skipping')
            continue
        county = _county_count_in_polygon(fetcher, rings)
        mine = stored.get(zip_code, 0)
        if county <= 0:
            logger.warning(f'[completeness] zip={zip_code} - county count unavailable, skipping')
            continue
        ratio = mine / county
        rows.append((zip_code, OC_ZIP_CITY.get(zip_code, '?'), mine, county, ratio))
        total_stored += mine
        total_county += county

    if not rows:
        logger.error('[completeness] no zips could be checked')
        return 1

    overall = total_stored / total_county
    logger.info(f'[completeness] overall: stored {total_stored:,} / county-in-bbox {total_county:,} '
                f'= {overall:.1%} retention (centroid-inside + junk-APN filtering means <100% is expected)')

    # Flag zips whose retention is anomalously low vs the fleet - those are
    # where parcels were likely lost, not just boundary-trimmed.
    flagged = [r for r in rows if r[4] < LOW_RETENTION_FLAG]
    for zip_code, city, mine, county, ratio in sorted(rows, key=lambda r: r[4]):
        marker = '  <-- LOW' if ratio < LOW_RETENTION_FLAG else ''
        logger.info(f'  {zip_code} {city:<20} stored={mine:>6,} county={county:>6,} '
                    f'ratio={ratio:.1%}{marker}')

    if flagged:
        logger.warning(f'[completeness] {len(flagged)} zip(s) below {LOW_RETENTION_FLAG:.0%} retention - '
                       f'likely dropped parcels; re-run ingest_oc_parcels for those zips.')
        return 1

    logger.info('[completeness] no zips anomalously low - ingestion looks complete')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Check OC ingestion completeness against the county source.')
    parser.add_argument('--zips', type=str, default=None,
                        help='Comma-separated zips (default: all in OC_ZIP_CITY)')
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    args = parser.parse_args()
    zips = args.zips.split(',') if args.zips else list(OC_ZIP_CITY.keys())
    sys.exit(run(zips, args.database_url))


if __name__ == '__main__':
    main()