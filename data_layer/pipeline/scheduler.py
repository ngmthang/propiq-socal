"""
    PropIQ - Pipeline Scheduler
    Runs the data pipeline on a schedule using APScheduler.
    Start with: python -m propiq.data_layer.pipeline.scheduler

    @author Minh Thang Nguyen
    @version June 21, 2026
"""

import os
import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from pipeline import DataPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)-30s %(levelname)-8s %(message)s',
)
logger = logging.getLogger('propiq.scheduler')

DATABASE_URL = os.getenv('DATABASE_URL', 'postgres://propiq:propiq@localhost:5432/propiq')
pipeline = DataPipeline(DATABASE_URL)
scheduler = BlockingScheduler(timezone='America/Los_Angeles')

# Jobs
@scheduler.scheduled_job(CronTrigger(day_of_week='sun', hour=2, minute=0))
def weekly_full_sync():
    """Full SoCal scrape every Sunday at 2 AM PT."""
    logger.info('=== WEEKLY FULL SYNC STARTING ===')
    pipeline.run_full_sync()
    logger.info('=== WEEKLY FULL SYNC COMPLETE ===')

@scheduler.scheduled_job(CronTrigger(hour=6, minute=0))
def daily_incremental():
    """Daily incremental sync at 6 AM PT - caches new listings fast."""
    logger.info('=== DAILY INCREMENTAL SYNC ===')
    pipeline.run_incremental()

@scheduler.scheduled_job(CronTrigger(hour='*/6', minute=30))
def feature_compute():
    """Compute missing features every 6 hours."""
    from sqlalchemy.orm import Session
    from data_layer.models.database import get_engine
    from feature_engineering import FeatureEngineer

    engine = get_engine(DATABASE_URL)
    fe = FeatureEngineer(engine)
    logger.info('=== FEATURE ENGINEERING RUN ===')
    with Session(engine) as session:
        fe.compute_all(session, batch_size=1000)

# Entrypoint
if __name__ == '__main__':
    logger.info('PropIQ pipeline scheduler starting...')
    logger.info('Jobs: weekly full sync (Sun 2AM), daily incremental (6AM), features (every 6h')
    scheduler.start()