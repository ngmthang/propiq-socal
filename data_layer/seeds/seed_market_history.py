"""
    PropIQ - Market History Seeder
    Generates 60 months of synthetic monthly market snapshots per seed zip,
    feeding the LSTM forecaster. 60 months because the trainer consumes a
    24-month lookback predicting 3/6/12-month-ahead changes: sequences per
    zip = months - 24 - 12 + 1, so 60 months -> 25 sequences/zip -> 500
    total across 20 zips (trainer minimum: 50). The previous 36-month
    window yielded exactly 1 per zip - mathematically untrainable.

    Series design: median price follows a per-zip random walk with mild
    annual drift + seasonality, ending near the zip's base price level so
    it stays consistent with seed_avm_data's neighborhoods. Inventory,
    DOM, and sales are seasonally coherent with each other (hot spring
    market = more sales, fewer days on market).

    Idempotent: deletes prior rows for the seeded zips, then reinserts.

    Usage:
        python -m data_layer.seeds.seed_market_history
        python -m data_layer.seeds.seed_market_history --months 72

    @author Minh Thang Nguyen
    @version August 2, 2026
"""

import os
import math
import random
import argparse
import logging
from datetime import datetime

from sqlalchemy import delete

from data_layer.models.database import MarketTrend, get_engine, get_session
from data_layer.seeds.seed_avm_data import SEED_ZIPS

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.seeds.market')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1)


def _generate_zip_series(rng: random.Random, zip_code: str, base_price: float,
                         months: int) -> list[dict]:
    """One coherent monthly series ending this month at ~base_price."""
    now = datetime.utcnow()
    annual_drift = rng.uniform(0.02, 0.07)          # this zip's long-run appreciation
    monthly_drift = (1 + annual_drift) ** (1 / 12) - 1

    # Walk backwards from today's level so the series ENDS at base_price
    # (consistent with Neighborhood.median_home_price from seed_avm_data).
    prices = [base_price * rng.uniform(0.98, 1.02)]
    for _ in range(months - 1):
        shock = rng.gauss(0, 0.012)                 # monthly noise ~1.2%
        prices.append(prices[-1] / (1 + monthly_drift + shock))
    prices.reverse()

    base_inventory = rng.randint(60, 220)
    rows = []
    for i in range(months):
        # calendar month for this point, walking back from current month
        offset = months - 1 - i
        year = now.year - (offset // 12) - (1 if now.month - (offset % 12) <= 0 else 0)
        month = (now.month - (offset % 12) - 1) % 12 + 1
        snapshot = _month_start(year, month)

        # spring/summer heat: peaks around June (month 6)
        season = math.sin((month - 3) / 12 * 2 * math.pi)   # -1..1, peak ~Jun
        inventory = max(15, int(base_inventory * (1 + 0.25 * season) * rng.uniform(0.85, 1.15)))
        closed = max(5, int(inventory * rng.uniform(0.25, 0.45) * (1 + 0.2 * season)))
        dom = max(8, int(38 - 12 * season + rng.gauss(0, 4)))
        absorption = round(min(0.95, max(0.05, closed / max(inventory, 1))), 3)
        list_to_sale = round(rng.uniform(0.965, 1.035) + 0.01 * season, 4)

        rows.append(dict(
            zip_code=zip_code,
            snapshot_date=snapshot,
            median_price=round(prices[i], -3),
            active_listings=inventory,
            closed_sales=closed,
            avg_dom=dom,
            list_to_sale=list_to_sale,
            absorption_rate=absorption,
        ))
    return rows


def run(months: int, database_url: str, rng_seed: int = 42) -> int:
    rng = random.Random(rng_seed)
    engine = get_engine(database_url)
    zips = list(SEED_ZIPS.keys())

    with get_session(engine) as session:
        deleted = session.execute(
            delete(MarketTrend).where(MarketTrend.zip_code.in_(zips))
        ).rowcount
        if deleted:
            logger.info(f'[market] cleared {deleted} previous rows for seeded zips')

        created = 0
        for zip_code, meta in SEED_ZIPS.items():
            base_price = meta[2]
            for row in _generate_zip_series(rng, zip_code, base_price, months):
                session.add(MarketTrend(**row))
                created += 1
        session.commit()

    seq_per_zip = months - 24 - 12 + 1
    logger.info(f'[market] created {created} snapshots ({months} months x {len(zips)} zips) '
                f'-> ~{seq_per_zip} LSTM sequences/zip, ~{seq_per_zip * len(zips)} total')
    return created


def main():
    parser = argparse.ArgumentParser(description='Seed synthetic monthly market history for the LSTM.')
    parser.add_argument('--months', type=int, default=60)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    args = parser.parse_args()
    run(args.months, args.database_url, args.seed)


if __name__ == '__main__':
    main()