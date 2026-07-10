"""
    PropIQ - Retraining Scheduler
    Schedule:
        - AVM (XGBoost): every Sunday at 3 AM - rolling 24-month window
        - LSTM: 1st of each month at 4 AM - rolling 36-month window
        - Model Eval: daily at 7 AM - track live MAPE drift

    @author Minh Thang Nguyen
    @version July 8, 2026
"""

from __future__ import annotations

import os, json
from datetime import datetime
from pathlib import Path
from typing import Optional
from loguru import logger

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..training.avm_trainer import AVMTrainer, AVMConfig
from ..training.lstm_trainer import LSTMTrainer, LSTMConfig
from ..inference.engine import InferenceEngine
from ..utils.db import load_training_data, load_market_history

MODEL_BASE = os.getenv("MODEL_DIR", "models/saved")

def retrain_avm(db_url: str, engine_ref: Optional[list] = None):
    version = datetime.utcnow().strftime("avm_%Y%m%d")
    path = f"{MODEL_BASE}/{version}"
    logger.info(f"AVM retrain starting -> {path}")
    try:
        df = load_training_data(db_url, months=24)
        if len(df) < 500:
            logger.warning(f"AVM retrain skipped: only {len(df)} row available");
            return

        trainer = AVMTrainer(AVMConfig())
        metrics = trainer.train(df)
        trainer.save(path)

        latest = Path(f"{MODEL_BASE}/avm_latest")
        if latest.is_symlink(): latest.unlink()
        latest.symlink_to(Path(path).resolve())

        if engine_ref and engine_ref[0]:
            old_avm = engine_ref[0].avm
            engine_ref[0].avm = AVMTrainer.load(path)
            logger.info(f"AVM hot-swapped | MAPE={metrics['mape']:.2%}")
            del old_avm

        _log_retrain_event("avm", version, metrics)
    except Exception as e:
        logger.error(f"AVM retrain failed: {e}")

def retrain_lstm(db_url: str, engine_ref: Optional[list] = None):
    version = datetime.utcnow().strftime("lstm_%Y%m%d")
    path = f"{MODEL_BASE}/{version}"
    logger.info(f"LSTM retrain starting -> {path}")
    try:
        market_df = load_market_history(db_url, months=36)
        if len(market_df) < 200:
            logger.warning(f"LSTM retrain skipped: only {len(market_df)} rows")
            return

        trainer = LSTMTrainer(LSTMConfig())
        metrics = trainer.train(market_df)
        trainer.save(path)

        lastest = Path(f"{MODEL_BASE}/lstm_latest")
        if lastest.is_symlink(): lastest.unlink()
        lastest.symlink_to(Path(path).resolve())

        if engine_ref and engine_ref[0]:
            old_lstm = engine_ref[0].lstm
            engine_ref[0].lstm = LSTMTrainer.load(path)
            logger.info(f"LSTM hot-swapped | {metrics}")
            del old_lstm

        _log_retrain_event("lstm", version, metrics)
    except Exception as e:
        logger.error(f"LSTM retrain failed: {e}")

def evaluate_live_accuracy(db_url: str, engine_ref: Optional[list] = None):
    logger.info("Running live accuracy evaluation")
    try:
        df = load_training_data(db_url, months=1)
        if len(df) < 20 or engine_ref is None or not engine_ref[0]: return

        from ..features.feature_builder import FeatureBuilder, TARGET_COL
        builder = FeatureBuilder()
        X,y = builder.build(df, target=TARGET_COL)
        if y is None: return

        preds = engine_ref[0].avm.model.predict(X)
        mape = float(abs((y.values - preds) / y.values.clip(min=1)).mean())
        logger.info(f"Live MAPE (last 30d): {mape:.2%}")
        if mape > 0.12:
            logger.warning(f"MAPE DRIFT ALERT: {mape:.2%} - consider emergency retrain")
    except Exception as e:
        logger.error(f"Live eval failed: {e}")


class MLScheduler:
    def __init__(self, db_url: str, engine_ref: Optional[list] = None):
        self.db_url = db_url
        self.engine_ref = engine_ref
        self._scheduler = BackgroundScheduler(timezone="American/Los_Angeles")

    def start(self):
        self._scheduler.add_job(
            func=retrain_avm, trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
            kwargs={"db_url": self.db_url, "engine_ref": self.engine_ref},
            id="avm_retrain", name="AVM Weekly Retrain", replace_existing=True,
        )
        self._scheduler.add_job(
            func=retrain_lstm, trigger=CronTrigger(day=1, hour=4, minute=0),
            kwargs={"db_url": self.db_url, "engine_ref": self.engine_ref},
            id="lstm_retrain", name="LSTM Weekly Retrain", replace_existing=True,
        )
        self._scheduler.add_job(
            func=evaluate_live_accuracy, trigger=CronTrigger(hour=7, minute=0),
            kwargs={"db_url": self.db_url, "engine_ref": self.engine_ref},
            id="live_eval", name="Daily Live Accuracy Eval", replace_existing=True,
        )
        self._scheduler.start()
        logger.info("MLScheduler started | AVM=Sunday@3AM, LSTM=1st@4Am, Eval=Daily@7AM")

    def shutdown(self):
        self._scheduler.shutdown(wait=False)
        logger.info("MLScheduler stopped")

    def run_now(self, job_id: str):
        job = self._scheduler.get_job(job_id)
        if job: job.func(**job.kwargs)
        else: raise ValueError(f"Unknown job_id: {job_id}")


def _log_retrain_event(model: str, version: str, metrics: dict):
    log_path = Path(f"{MODEL_BASE}/retrain_log.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps({"model": model, "version": version,
                            "metrics": metrics,
                            "logged_at": datetime.utcnow().isoformat()}) + "\n")