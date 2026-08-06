"""
    PropIQ - Redfin Enrichment CLI
    Matches Redfin listings against existing real OC parcels (by normalized
    address, within the same zip) and backfills:
      - Property.list_price   <- Redfin's active listing price (real)
      - Property.last_sale_price / last_sale_date <- Redfin's sold price (real)
      - building_sqft / lot_size_sqft / bedrooms / bathrooms / year_built,
        but ONLY where the existing county-sourced value is missing —
        Redfin never overwrites a real county number.

    This exists because OC's public parcel layer (see oc_parcel_fetcher.py)
    does not expose square footage, lot size, or bathrooms at all, and has
    no price data whatsoever (CA assessors don't publicly disclose sale
    prices). Redfin is the source of real, market-observed prices for
    PropIQ. Coverage is necessarily partial: only parcels that are
    currently listed or sold within the lookback window will match.

    Matching is intentionally conservative: exact match on a normalized
    (house number + street) key, never fuzzy. A wrong match would silently
    attach one property's real price to a different parcel, which is worse
    than just leaving a parcel unpriced. Ambiguous keys (matching more than
    one existing Property in the same zip) are skipped, not guessed.

    Usage:
        python -m data_layer.scrapers.enrich_from_redfin
        python -m data_layer.scrapers.enrich_from_redfin --zip-codes 92602,92603
        python -m data_layer.scrapers.enrich_from_redfin --dry-run

    @author Minh Thang Nguyen
    @version August 5, 2026
"""

import os
import argparse
import logging
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import select

from data_layer.models.database import Property, ScrapeJob, get_engine, get_session
from data_layer.scrapers.address_matching import build_zip_index, normalize_address
from data_layer.scrapers.oc_parcel_fetcher import OC_ZIP_CITY
from data_layer.scrapers.redfin_scraper import RedfinScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.ingest.redfin_enrich')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')

# Only enrich parcels that came from the real county source - never touch
# synthetic seed rows, which the AVM's leakage-free training relies on
# staying exactly as generated.
ENRICHABLE_SOURCE = 'oc_parcel_gis'


def _parse_redfin_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ('%B-%d-%Y', '%Y-%m-%d', '%m/%d/%Y', '%b-%d-%Y'):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    logger.debug(f'[redfin_enrich] unrecognized date format: {raw!r}')
    return None

def build_zip_index(session, zip_code: str) -> dict[str, list[Property]]:
    """normalized address -> matching real Property rows in this zip."""
    rows = session.execute(
        select(Property).where(
            Property.zip_code == zip_code,
            Property.data_source == ENRICHABLE_SOURCE,
        )
    ).scalars().all()

    index: dict[str, list[Property]] = defaultdict(list)
    for prop in rows:
        key = normalize_address(prop.address, city_hint=prop.city)
        if key:
            index[key].append(prop)
    return index


def apply_enrichment(prop: Property, item: dict, stats: Counter) -> bool:
    """Mutates `prop` in place from a parsed Redfin listing. Returns True
    if anything actually changed."""
    changed = False

    sold_price = item.get('last_sale_price')
    if sold_price:
        prop.last_sale_price = sold_price
        parsed_date = _parse_redfin_date(item.get('last_sale_date'))
        if parsed_date:
            prop.last_sale_date = parsed_date
        stats['sold_price_set'] += 1
        changed = True
    else:
        # RedfinScraper.parse_listing() puts the CSV 'PRICE' column here -
        # for a row with no sold price, that's the active listing price.
        active_price = item.get('estimated_value')
        if active_price:
            prop.list_price = active_price
            stats['list_price_set'] += 1
            changed = True

    for field in ('building_sqft', 'lot_size_sqft', 'bathrooms', 'bedrooms', 'year_built'):
        val = item.get(field)
        if val and not getattr(prop, field, None):
            setattr(prop, field, val)
            stats[f'{field}_backfilled'] += 1
            changed = True

    if changed:
        prop.updated_at = datetime.utcnow()
        stats['properties_enriched'] += 1
    return changed


def run(zip_codes: list[str], database_url: str, dry_run: bool = False) -> dict:
    scraper = RedfinScraper()
    listings, fetch_stats = scraper.run(zip_codes)
    logger.info(f"[redfin_enrich] fetched/parsed {fetch_stats['parsed']}/{fetch_stats['fetched']} "
                f"listings in {fetch_stats['duration_secs']:.1f}s")

    by_zip: dict[str, list[dict]] = defaultdict(list)
    for item in listings:
        zc = item.get('zip_code')
        if zc:
            by_zip[zc].append(item)

    stats: Counter = Counter()

    if dry_run:
        sample = listings[:3]
        logger.info(f'[redfin_enrich] --dry-run: not writing to the database. Sample: {sample}')
        return {'matched': 0, 'unmatched': len(listings), 'ambiguous': 0, **fetch_stats}

    engine = get_engine(database_url)
    job = ScrapeJob(source='redfin_enrich', job_type='enrichment', status='running',
                     records_fetched=fetch_stats['fetched'])
    with get_session(engine) as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    started = datetime.utcnow()
    try:
        with get_session(engine) as session:
            for zip_code, items in by_zip.items():
                zip_index = build_zip_index(session, zip_code, ENRICHABLE_SOURCE)
                for item in items:
                    key = normalize_address(item.get('address'))
                    candidates = zip_index.get(key, []) if key else []

                    if not candidates:
                        stats['unmatched'] += 1
                        continue
                    if len(candidates) > 1:
                        stats['ambiguous'] += 1
                        continue

                    if apply_enrichment(candidates[0], item, stats):
                        stats['matched'] += 1
                    else:
                        stats['matched_no_new_data'] += 1

                logger.info(
                    f'[redfin_enrich] zip={zip_code} ({OC_ZIP_CITY.get(zip_code, "?")}) - '
                    f'{len(items)} redfin rows processed'
                )
            session.commit()

        with get_session(engine) as session:
            j = session.get(ScrapeJob, job_id)
            j.status = 'success'
            j.records_saved = 0
            j.records_updated = stats['matched']
            j.records_skipped = stats['unmatched'] + stats['ambiguous']
            j.completed_at = datetime.utcnow()
            j.duration_secs = (datetime.utcnow() - started).total_seconds()
            session.commit()

    except Exception as e:
        logger.error(f'[redfin_enrich] enrichment failed: {e}')
        with get_session(engine) as session:
            j = session.get(ScrapeJob, job_id)
            j.status = 'failed'
            j.error_log = str(e)
            j.completed_at = datetime.utcnow()
            session.commit()
        raise

    logger.info(
        f"[redfin_enrich] done - matched={stats['matched']} "
        f"(sold_price_set={stats['sold_price_set']}, list_price_set={stats['list_price_set']}) "
        f"unmatched={stats['unmatched']} ambiguous={stats['ambiguous']}"
    )
    return {**stats, **fetch_stats}


def main():
    parser = argparse.ArgumentParser(
        description='Enrich real OC parcels with real Redfin prices/attributes, matched by address.'
    )
    parser.add_argument(
        '--zip-codes', type=str, default=None,
        help='Comma-separated zip codes. Defaults to every zip in OC_ZIP_CITY.',
    )
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    parser.add_argument('--dry-run', action='store_true', help='Fetch and match but skip DB writes.')
    args = parser.parse_args()

    zip_codes = args.zip_codes.split(',') if args.zip_codes else list(OC_ZIP_CITY.keys())
    run(zip_codes, args.database_url, dry_run=args.dry_run)


if __name__ == '__main__':
    main()