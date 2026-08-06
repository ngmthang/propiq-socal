"""
    PropIQ - Batch AVM Scoring
    Runs the already-trained AVM against every property that meets
    AVM_REQUIRED_FIELDS (physical attributes present), and persists the
    prediction to Property.estimated_value.

    This is inference only - it does NOT retrain anything and does NOT
    require a known sale price (that's exactly the point: it scores the
    properties we don't already have a real price for, e.g. the real OC
    parcels Zillow-enriched with sqft/beds/baths but no sold price).
    Run scheduler.py --job avm first/instead if you want to retrain the
    model itself on new labeled data.

    Vectorized: builds the whole feature matrix and predicts in one
    XGBoost call rather than looping engine.valuate() per row (which also
    runs SHAP - far too slow at thousands of rows for what's just a bulk
    "give me a number" job with no per-row explanation needed).

    Usage:
        python -m ml_layer.training.batch_score
        python -m ml_layer.training.batch_score --dry-run
        python -m ml_layer.training.batch_score --avm-path models/saved/avm/latest

    @author Minh Thang Nguyen
    @version August 6, 2026
"""

from __future__ import annotations

import os
import argparse
import logging
from datetime import datetime

from data_layer.models.database import Property, get_engine, get_session
from ml_layer.features.feature_builder import FeatureBuilder
from ml_layer.training.avm_trainer import AVMTrainer
from ml_layer.utils.db import load_scoring_data

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('propiq.ml.batch_score')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://propiq:propiq@localhost:5433/propiq')


def run(avm_path: str, database_url: str, dry_run: bool = False) -> dict:
    trainer = AVMTrainer.load(avm_path)
    df = load_scoring_data(database_url)

    if df.empty:
        logger.warning('No AVM-ready properties found to score.')
        return {'scored': 0, 'written': 0}

    builder = FeatureBuilder()
    X, _ = builder.build(df)
    preds = trainer.model.predict(X)
    df = df.assign(predicted_value=[round(float(p), -2) for p in preds])

    logger.info(
        f"Scored {len(df)} properties | "
        f"min=${df.predicted_value.min():,.0f} "
        f"median=${df.predicted_value.median():,.0f} "
        f"max=${df.predicted_value.max():,.0f}"
    )

    if dry_run:
        sample = df[['id', 'address', 'zip_code', 'predicted_value']].head(5).to_dict('records')
        logger.info(f'--dry-run: not writing to the database. Sample: {sample}')
        return {'scored': len(df), 'written': 0}

    engine = get_engine(database_url)
    now = datetime.utcnow()
    mappings = [
        {'id': int(row.id), 'estimated_value': row.predicted_value, 'updated_at': now}
        for row in df.itertuples()
    ]
    with get_session(engine) as session:
        session.bulk_update_mappings(Property, mappings)
        session.commit()

    logger.info(f'Wrote estimated_value for {len(mappings)} properties.')
    return {'scored': len(df), 'written': len(mappings)}


def main():
    parser = argparse.ArgumentParser(
        description='Batch-score the trained AVM against every AVM-ready property.'
    )
    parser.add_argument(
        '--avm-path', type=str,
        default=os.getenv('AVM_MODEL_PATH', 'models/saved/avm/latest'),
    )
    parser.add_argument('--database-url', type=str, default=DATABASE_URL)
    parser.add_argument('--dry-run', action='store_true', help='Predict and log but skip DB writes.')
    args = parser.parse_args()

    run(args.avm_path, args.database_url, dry_run=args.dry_run)


if __name__ == '__main__':
    main()