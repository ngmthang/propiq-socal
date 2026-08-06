"""
    PropIQ - Zillow Enrichment CLI
    Same purpose as enrich_from_redfin.py, different source: matches
    Zillow "For Sale" listings (via RapidAPI - a legitimate authorized
    gateway, not a raw site scrape) against existing real OC parcels by
    normalized address, and backfills:
      - Property.list_price   <- Zillow's listing price / zestimate (real)
      - Property.assessed_value <- Zillow's tax-assessed value, if we don't
        already have one from the county
      - building_sqft / lot_size_sqft / bedrooms / bathrooms / year_built,
        but ONLY where the existing county-sourced value is missing.

    Why this exists instead of just using Redfin: Redfin's stingray CSV
    endpoint is undocumented and, as of Aug 2026, is blocked outright by
    CloudFront (403, confirmed both inside and outside Docker - a WAF/TLS-
    fingerprint block, not a config issue on our end). Zillow via RapidAPI
    goes through an authorized API gateway instead of scraping Zillow's own
    site, so it isn't subject to that kind of edge blocking.

    Zillow's ZLLW Working API only returns active ("For_Sale") listings, no
    sold-price history - so unlike Redfin enrichment, this never sets
    last_sale_price. It also does NOT have a --dry-run request-count
    exemption: every zip costs 1 request even in dry-run, since there's no
    separate free "does this endpoint respond" check. Be deliberate about
    zip-code batches - the free RapidAPI tier is 500 req/month, and this
    project tracks ~87 OC zips, so one full pass is ~87 requests
    (ZillowScraper's default max_pages=1 - raising it multiplies the cost).

    Usage:
        python -m data_layer.scrapers.enrich_from_zillow
        python -m data_layer.scrapers.enrich_from_zillow --zip-codes 92602,92603
        python -m data_layer.scrapers.enrich_from_zillow --dry-run

    @author Minh Thang Nguyen
    @version August 5, 2026
"""

import os
import argparse
import logging
from collections import Counter, defaultdict
from datetime import datetime

from data_layer.models.database import Property, ScrapeJob, get_engine, get_session
from data_layer.scrapers.address_matching import build_zip_index, normalize_address
from data_layer.scrapers.oc_parcel_fetcher import OC_ZIP_CITY
from data_layer.scrapers.zillow_scraper import ZillowScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.ingest.zillow_enrich')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')

# Only enrich parcels that came from the real county source - never touch
# synthetic seed rows, which the AVM's leakage-free training relies on
# staying exactly as generated.
ENRICHABLE_SOURCE = 'oc_parcel_gis'


def apply_enrichment(prop: Property, item: dict, stats: Counter) -> bool:
    """Mutates `prop` in place from a parsed Zillow listing. Returns True
    if anything actually changed."""
    changed = False

    # Every row from this scraper is an active For_Sale listing (see module
    # docstring) - estimated_value here is Zillow's list price / zestimate,
    # which is exactly what Property.list_price is for.
    active_price = item.get('estimated_value')
    if active_price:
        prop.list_price = active_price
        stats['list_price_set'] += 1
        changed = True

    tax_value = item.get('tax_assessed_value')
    if tax_value and not prop.assessed_value:
        prop.assessed_value = tax_value
        stats['assessed_value_backfilled'] += 1
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


def run(zip_codes: list[str], database_url: str, dry_run: bool = False, max_pages: int = 1) -> dict:
    scraper = ZillowScraper(max_pages=max_pages)
    listings, fetch_stats = scraper.run(zip_codes)
    logger.info(f"[zillow_enrich] fetched/parsed {fetch_stats['parsed']}/{fetch_stats['fetched']} "
                f"listings in {fetch_stats['duration_secs']:.1f}s "
                f"(~{len(zip_codes) * max_pages} RapidAPI requests spent)")

    by_zip: dict[str, list[dict]] = defaultdict(list)
    for item in listings:
        zc = item.get('zip_code')
        if zc:
            by_zip[zc].append(item)

    stats: Counter = Counter()

    if dry_run:
        sample = listings[:3]
        logger.info(f'[zillow_enrich] --dry-run: not writing to the database. Sample: {sample}')
        return {'matched': 0, 'unmatched': len(listings), 'ambiguous': 0, **fetch_stats}

    engine = get_engine(database_url)
    job = ScrapeJob(source='zillow_enrich', job_type='enrichment', status='running',
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
                    f'[zillow_enrich] zip={zip_code} ({OC_ZIP_CITY.get(zip_code, "?")}) - '
                    f'{len(items)} zillow rows processed'
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
        logger.error(f'[zillow_enrich] enrichment failed: {e}')
        with get_session(engine) as session:
            j = session.get(ScrapeJob, job_id)
            j.status = 'failed'
            j.error_log = str(e)
            j.completed_at = datetime.utcnow()
            session.commit()
        raise

    logger.info(
        f"[zillow_enrich] done - matched={stats['matched']} (list_price_set={stats['list_price_set']}) "
        f"unmatched={stats['unmatched']} ambiguous={stats['ambiguous']}"
    )
    return {**stats, **fetch_stats}


def main():
    parser = argparse.ArgumentParser(
        description='Enrich real OC parcels with real Zillow listing prices/attributes, matched by address.'
    )
    parser.add_argument(
        '--zip-codes', type=str, default=None,
        help='Comma-separated zip codes. Defaults to every zip in OC_ZIP_CITY (~87 zips = ~87 requests).',
    )
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    parser.add_argument('--dry-run', action='store_true', help='Fetch and match but skip DB writes.')
    parser.add_argument(
        '--max-pages', type=int, default=1,
        help='Pages per zip (1 page per RapidAPI request). Keep at 1 to conserve free-tier quota.',
    )
    args = parser.parse_args()

    zip_codes = args.zip_codes.split(',') if args.zip_codes else list(OC_ZIP_CITY.keys())
    run(zip_codes, args.database_url, dry_run=args.dry_run, max_pages=args.max_pages)


if __name__ == '__main__':
    main()