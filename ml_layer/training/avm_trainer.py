"""
    PropIQ - XGBoost Automated Valuation Model (AVM)
    Predicts fair market value (sale_price) for a property.

    Model pipeline:
        1. FeatureBuilder -> feature matrix X
        2. Preprocessing -> StandardScaler on numerical cols
        3. XGBRegressor -> predicted sale price
        4. SHAP -> feature importance + per-prediction explanation

    @author Minh Thang Nguyen
    @version June 22, 2026
"""

from __future__ import annotations

import os, json, joblib, shap
import numpy as np
import pandas as pd
import xgboost as xgb

from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from sklearn.model_selection import KFold, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score
from loguru import logger

from ..features.feature_builder import FeatureBuilder, ALL_FEATURE_COLS, TARGET_COL

@dataclass
class AVMConfig:
    n_estimators: int = 800
    max_depth: int = 6
    learning_rate: float = 0.05
    subsample: float = 0.8
    colsample_bytree: float = 0.7
    min_child_weight: int = 10
    reg_alpha: float = 0.1
    reg_lambda: float = 1.0
    early_stopping: int = 50
    tree_method: str = 'hist'
    cv_folds: int = 5
    test_size: float = 0.15
    random_state: int = 42
    model_dir: str = 'models/saved'

class AVMTrainer:
    def __init__(self, config: Optional[AVMConfig] = None):
        self.config = config or AVMConfig()
        self.builder = FeatureBuilder()
        self.model: Optional[Pipeline] = None
        self.explainer: Optional[shap.TreeExplainer] = None
        self._train_metrics: dict = {}

    def train(self, df: pd.DataFrame) -> dict:
        logger.info(f'AVM training started | rows={len(df)}')
        X, y = self.builder.build(df, target=TARGET_COL)
        if y is None or y.isna().all():
            raise ValueError('No valid sale_price values in training data')

        mask = y.notna() & (y > 50_000) & (y < 50_000_000)
        X, y = X[mask], y[mask]
        logger.info(f'After price filter: {len(X)} rows')

        split = int(len(X) * (1 - self.config.test_size))
        X_train, X_val = X.iloc[:split], X.iloc[split:]
        y_train, y_val = y.iloc[split:], y.iloc[split:]

        xgb_params = dict(
            n_estimators=self.config.n_estimators, max_depth=self.config.max_depth,
            learning_rate=self.config.learning_rate, subsample=self.config.subsample,
            colsample_bytree=self.config.colsample_bytree, min_child_weight=self.config.min_child_weight,
            reg_alpha=self.config.reg_alpha, reg_lambda=self.config.reg_lambda,
            tree_method=self.config.tree_method, random_state=self.config.random_state,
            eval_metric="mae", early_stopping_rounds=self.config.early_stopping,
        )
        xgb_model = xgb.XGBRegressor(**xgb_params)
        self.model = Pipeline([('scaler', StandardScaler()), ('xgb', xgb_model)])

        self.model.fit(
            X_train, y_train,
            xgb__eval_set=[(self.model.named_steps['scaler'].fit_transform(X_val), y_val)],
            xgb__verbose=False,
        )

        y_pred = self.model.predict(X_val)
        metrics = self._evaluate(y_val, y_pred)
        self._train_metrics = metrics

        cv_mae = self._cross_validate(X, y)
        metrics['cv_mae_mean'] = float(np.mean(cv_mae))
        metrics['cv_mae_std'] = float(np.std(cv_mae))

        raw_xgb = self.model.named_steps['xgb']
        self.explainer = shap.TreeExplainer(raw_xgb)

        logger.info(f"AVM training done |"
                    f" MAPE={metrics['mape']:.2%} |"
                    f" MAE=${metrics['mae']:,.0f} |"
                    f" R²={metrics['r2']:.3f}")
        return metrics

    def _evaluate(self, y_true: pd.Series, y_pred: np.ndarray) -> dict:
        return {
            'mae': float(mean_absolute_error(y_true, y_pred)),
            'mape': float(mean_absolute_percentage_error(y_true, y_pred)),
            'r2': float(r2_score(y_true, y_pred)),
            'median_error': float(np.mean(np.abs(y_true.values - y_pred))),
            'within_5pct': float(np.mean(np.abs(y_true.values - y_pred) / y_true.values < 0.05)),
            'within_10pct': float(np.mean(np.abs(y_true.values - y_pred) / y_true.values < 0.1)),
            'trained_at': datetime.utcnow().isoformat(),
            'n_train': len(y_true),
        }

    def _cross_validate(self, X: pd.DataFrame, y: pd.Series) -> list[float]:
        kf = KFold(n_splits=self.config.cv_folds, shuffle=True, random_state=self.config.random_state)
        xgb_model = xgb.XGBRegressor(
            n_estimators=300, max_depth=self.config.max_depth,
            learning_rate=self.config.learning_rate, tree_method=self.config.tree_method,
            random_state=self.config.random_state, verbosity=0,
        )
        pipeline = Pipeline([('scaler', StandardScaler()), ('xgb', xgb_model)])
        scores = cross_val_score(pipeline, X, y, cv=kf, scoring='neg_mean_absolute_error', n_jobs=-1)
        return list(-scores)

    def feature_importance(self) -> pd.DataFrame:
        if not self.model:
            raise RuntimeError('Model not trained yet')
        imp = self.model.named_steps['xgb'].feature_importances_
        return (
            pd.DataFrame({'feature': ALL_FEATURE_COLS, 'importance': imp})
            .sort_values('importance', ascending=False)
            .reset_index(drop=True)
        )

    def save(self, path: str):
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, p / 'pipeline.joblib')
        joblib.dump(self.explainer, p / 'shap_explainer.joblib')
        with open(p / 'metrics.json', 'w') as f:
            json.dump(self._train_metrics, f, indent=2)
        logger.info(f'AVM saved to {p}')

    @classmethod
    def load(cls, path: str) -> 'AVMTrainer':
        p = Path(path)
        trainer = cls()
        trainer.model = joblib.load(p / 'pipeline.joblib')
        trainer.explainer = joblib.load(p / 'shap_explainer.joblib')
        with open(p / 'metrics.json') as f:
            trainer._train_metrics = json.load(f)
        logger.info(f'AVM loaded from {p}')
        return trainer
