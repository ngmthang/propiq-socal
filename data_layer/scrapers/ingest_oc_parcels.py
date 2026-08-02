"""
    PropIQ - OC Parcel Ingestion CLI
    Runs OcParcelFetcher for a set of zip codes and upserts the results
    into the properties table, keyed by APN (parcel_number).

    Deliberately NOT wired through DataPipeline._upsert_property: that
    method never sets owner_id or zoning, both NOT NULL on Property, and
    it dedupes on address+zip rather than APN. Real parcel data gives us a
    much better key (APN) than address matching, so this script upserts on
    its own. Worth revisiting DataPipeline itself before wiring Zillow/
    Redfin into real (non-seed) runs, since they'll hit the same gap.

    Usage:
        python -m data_layer.scrapers.ingest_oc_parcels
        python -m data_layer.scrapers.ingest_oc_parcels --zip-codes 92602,92603
        python -m data_layer.scrapers.ingest_oc_parcels --dry-run

    @author Minh Thang Nguyen
    @version August 1, 2026
"""

import os
import argparse
import logging
from collections import Counter
from datetime import datetime

from sqlalchemy import select

from data_layer.models.database import (
    Property, PropertyType, ZoningType, User, UserRole, ScrapeJob,
    get_engine, get_session,
)
from data_layer.scrapers.oc_parcel_fetcher import OcParcelFetcher, OC_ZIP_CITY

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.ingest.oc_parcels')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')

# OC's public parcel layer doesn't expose zoning or use-type, so real
# parcels get a placeholder until a zoning-layer join replaces this.
# OC Public Works does publish other layers under
# https://www.ocgis.com/arcpub/rest/services - worth checking for an
# actual zoning MapServer as a follow-up.
DEFAULT_PROPERTY_TYPE = PropertyType.SINGLE_FAMILY
DEFAULT_ZONING = ZoningType.UNKNOWN

SYSTEM_OWNER_EMAIL = 'oc-parcel-import@propiq.internal'


def get_or_create_system_owner(session) -> User:
    """Parcel records have no PropIQ user attached to them, but owner_id is
    NOT NULL on Property. Use one shared system account as the owner for
    all county-sourced (as opposed to user-added) parcels."""
    owner = session.execute(
        select(User).where(User.email == SYSTEM_OWNER_EMAIL)
    ).scalar_one_or_none()
    if owner:
        return owner

    owner = User(
        email=SYSTEM_OWNER_EMAIL,
        full_name='OC Parcel Import (System)',
        password_hash='!disabled!',  # not a real login - never hashed/used for auth
        role=UserRole.CLIENT,
        is_active=False,
    )
    session.add(owner)
    session.flush()
    logger.info(f'[oc_parcels] created system owner user id={owner.id}')
    return owner


def upsert_parcel(session, data: dict, owner_id: int) -> str:
    """Insert or update a Property by APN (parcel_number). Returns
    'created', 'updated', or 'skipped'."""
    apn = data.get('parcel_number')
    if not apn:
        return 'skipped'

    zoning = ZoningType[data['zoning']] if data.get('zoning') else None

    prop = session.execute(
        select(Property).where(Property.parcel_number == apn)
    ).scalar_one_or_none()

    if prop:
        # Real county data wins over anything stale, but never blank out a
        # field we don't have new data for.
        for field in ('address', 'city', 'zip_code', 'county', 'latitude',
                      'longitude', 'year_built', 'bedrooms', 'units'):
            if data.get(field) is not None:
                setattr(prop, field, data[field])

        if zoning is not None:
            prop.zoning = zoning

        prop.data_source = data.get('source')
        prop.source_url = data.get('source_url', '')
        prop.raw_data = data.get('raw_data')
        prop.updated_at = datetime.utcnow()
        return 'updated'

    if not data.get('address') or not data.get('zip_code'):
        return 'skipped'  # not enough to satisfy NOT NULL columns

    prop = Property(
        owner_id=owner_id,
        address=data['address'],
        city=data.get('city', ''),
        state=data.get('state', 'CA'),
        zip_code=data['zip_code'],
        county=data.get('county', 'Orange'),
        latitude=data.get('latitude'),
        longitude=data.get('longitude'),
        parcel_number=apn,
        property_type=DEFAULT_PROPERTY_TYPE,
        zoning=zoning or DEFAULT_ZONING,
        year_built=data.get('year_built'),
        bedrooms=data.get('bedrooms'),
        units=data.get('units', 1),
        data_source=data.get('source'),
        source_url=data.get('source_url', ''),
        raw_data=data.get('raw_data'),
        is_verified=True,  # sourced from county public records, not a scrape guess
    )
    session.add(prop)
    return 'created'


def run(zip_codes: list[str], database_url: str, dry_run: bool = False) -> dict:
    fetcher = OcParcelFetcher()
    listings, fetch_stats = fetcher.run(zip_codes)
    logger.info(f"[oc_parcels] fetched/parsed {fetch_stats['parsed']}/{fetch_stats['fetched']} parcels "
                f"in {fetch_stats['duration_secs']:.1f}s")

    # A prior run showed a large 'updated' count on a table that should
    # have been empty, which only makes sense if the same APN appears more
    # than once in a single fetched batch. Investigation confirmed this is
    # real signal, not noise: APNs with high repeat counts (e.g. 17x) are
    # almost certainly multi-unit apartment/condo complexes taxed as one
    # legal parcel, with each unit getting its own SITE_ADDRESS row. We
    # collapse to one Property row per APN (matching PropIQ's parcel-level
    # data model), but use the repeat count as that property's unit count
    # rather than discarding it - a real multi-family signal we'd
    # otherwise silently throw away. This also means the surviving
    # `address` for a multi-unit parcel is just one arbitrary unit's
    # address, not the complex's - a known limitation of not having a
    # separate "complex-level" address field.
    apn_counts = Counter(item.get('parcel_number') for item in listings if item.get('parcel_number'))
    dupes = {apn: n for apn, n in apn_counts.items() if n > 1}
    if dupes:
        total_dupe_records = sum(dupes.values())
        example_apns = list(dupes.items())[:5]
        logger.warning(
            f'[oc_parcels] {len(dupes)} APNs appear more than once in this batch '
            f'({total_dupe_records} records total) - treating repeat count as unit count '
            f'for that parcel. Examples (apn, count): {example_apns}'
        )
    for item in listings:
        apn = item.get('parcel_number')
        if apn in apn_counts:
            item['units'] = apn_counts[apn]

    if dry_run:
        logger.info(f'[oc_parcels] --dry-run: not writing to the database. Sample: {listings[:3]}')
        return {'created': 0, 'updated': 0, 'skipped': len(listings), **fetch_stats}

    engine = get_engine(database_url)
    job = ScrapeJob(source='oc_parcel_gis', job_type='full_sync', status='running',
                     records_fetched=fetch_stats['fetched'])

    with get_session(engine) as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    stats = {'created': 0, 'updated': 0, 'skipped': 0}
    started = datetime.utcnow()

    try:
        with get_session(engine) as session:
            owner = get_or_create_system_owner(session)
            session.flush()
            owner_id = owner.id

            for item in listings:
                try:
                    result = upsert_parcel(session, item, owner_id)
                    stats[result] += 1
                except Exception as e:
                    stats['skipped'] += 1
                    logger.warning(f'[oc_parcels] upsert error for {item.get("parcel_number")}: {e}')

            session.commit()

        with get_session(engine) as session:
            j = session.get(ScrapeJob, job_id)
            j.status = 'success'
            j.records_saved = stats['created']
            j.records_updated = stats['updated']
            j.records_skipped = stats['skipped']
            j.completed_at = datetime.utcnow()
            j.duration_secs = (datetime.utcnow() - started).total_seconds()
            session.commit()

    except Exception as e:
        logger.error(f'[oc_parcels] ingestion failed: {e}')
        with get_session(engine) as session:
            j = session.get(ScrapeJob, job_id)
            j.status = 'failed'
            j.error_log = str(e)
            j.completed_at = datetime.utcnow()
            session.commit()
        raise

    logger.info(f"[oc_parcels] done - created={stats['created']} updated={stats['updated']} "
                f"skipped={stats['skipped']}")
    return {**stats, **fetch_stats}


def main():
    parser = argparse.ArgumentParser(description='Ingest real OC parcel data from the public ArcGIS layer.')
    parser.add_argument(
        '--zip-codes',
        type=str,
        default=None,
        help='Comma-separated zip codes to ingest. Defaults to every zip in OC_ZIP_CITY.',
    )
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    parser.add_argument('--dry-run', action='store_true', help='Fetch and parse but skip DB writes.')
    args = parser.parse_args()

    zip_codes = args.zip_codes.split(',') if args.zip_codes else list(OC_ZIP_CITY.keys())
    run(zip_codes, args.database_url, dry_run=args.dry_run)


if __name__ == '__main__':
    main()