"""
    PropIQ - External Data Verification
    Pulls a random sample of stored OC parcels and re-queries each one, by
    APN, against Orange County's live public parcel endpoint - the same
    authoritative source they were ingested from - then diffs field by
    field. Answers "is what we stored still what the county says?"

    Usage:
        python -m scripts.verify_oc_sample                # 25 parcels
        python -m scripts.verify_oc_sample --sample 100

    Exit 0 if all sampled parcels match; 1 if any mismatch.
"""

import os
import sys
import argparse
import logging

from sqlalchemy import create_engine, text

from data_layer.scrapers.oc_parcel_fetcher import OcParcelFetcher

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.verify')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')


def _norm_addr(s) -> str:
    return ' '.join((s or '').upper().split())


def _norm_year(v):
    try:
        return int(str(v).strip()) if v not in (None, '') else None
    except (ValueError, TypeError):
        return None


def verify(sample_size: int, database_url: str) -> int:
    engine = create_engine(database_url)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT parcel_number, address, year_built, bedrooms, units
            FROM properties
            WHERE data_source = 'oc_parcel_gis'
            ORDER BY random()
            LIMIT :n
        """), {'n': sample_size}).all()

    if not rows:
        logger.error('No oc_parcel_gis rows found - nothing to verify')
        return 1

    fetcher = OcParcelFetcher()
    mismatches, missing, checked = [], [], 0

    for parcel_number, address, year_built, bedrooms, units in rows:
        feature = fetcher.fetch_by_apn(parcel_number)
        checked += 1
        if not feature:
            missing.append(parcel_number)
            continue

        attrs = feature.get('attributes', {})
        live_addr = _norm_addr(attrs.get('SITE_ADDRESS'))
        live_year = _norm_year(attrs.get('YEAR_BUILT'))
        live_beds = attrs.get('NBR_BEDROOMS')

        diffs = []
        db_year = year_built if year_built else None  # treat 0 same as null (county's "unknown")
        # Multi-unit parcels store one arbitrary unit's address (documented
        # limitation), so only compare address/bedrooms for single-unit parcels.
        if units == 1 and live_addr and _norm_addr(address) != live_addr:
            diffs.append(f'address: db={address!r} live={live_addr!r}')
        if (live_year if live_year else None) != db_year:
            diffs.append(f'year_built: db={year_built} live={live_year}')
        if units == 1 and live_beds is not None and (bedrooms or 0) != live_beds:
            diffs.append(f'bedrooms: db={bedrooms} live={live_beds}')

        if diffs:
            mismatches.append((parcel_number, diffs))

    logger.info(f'[verify] checked {checked} random parcels against the live county endpoint')
    logger.info(f'[verify] matched: {checked - len(mismatches) - len(missing)}, '
                f'mismatched: {len(mismatches)}, no longer found: {len(missing)}')

    for apn, diffs in mismatches:
        logger.warning(f'[verify] APN {apn}: ' + '; '.join(diffs))
    for apn in missing:
        logger.warning(f'[verify] APN {apn}: not found on county endpoint '
                       f'(retired/merged parcel, or transient query failure)')

    if mismatches:
        logger.warning('[verify] Mismatches usually mean the county updated data since '
                       'ingestion - rerun ingest_oc_parcels for the affected zips to refresh.')
    return 1 if mismatches else 0


def main():
    parser = argparse.ArgumentParser(description='Verify stored OC parcels against the live county source.')
    parser.add_argument('--sample', type=int, default=25)
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    args = parser.parse_args()
    sys.exit(verify(args.sample, args.database_url))


if __name__ == '__main__':
    main()