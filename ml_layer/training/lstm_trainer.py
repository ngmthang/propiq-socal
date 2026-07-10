"""
    PropIQ - LSTM Market Trend Forecaster
    Predicts median price changes for ZIP code over 3, 6, and 12 months

    Architecture:
        Input: rolling 24-month window of market features per ZIP
        Model: 2-layer LSTM -> dropout -> dense head
        Output: [Δ3mo%, Δ6mo%, Δ12mo%] - signed percentage changes

    Training cadence: monthly retrain.
    Data source: MarketTrend table (from data_layer).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import joblib, json

from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from loguru import logger
from sklearn.preprocessing import MinMaxScaler

@dataclass
class LSTMConfig:
    lookback_months: int = 24
    forecast_horizons: list = None
    hidden_size: int = 128
    num_layers: int = 2
    dropout: float = 0.2
    bidirectional: bool = False
    epochs: int = 100
    batch_size: int = 32
    lr: float = 1e-3
    weight_decay: float = 1e-4
    patience: int = 15
    feature_cols: list = None

    def __post_init__(self):
        if self.forecast_horizons is None:
            self.forecast_horizons = [3, 6, 12]
        if self.feature_cols is None:
            self.feature_cols = [
                'median_price', 'median_price_per_sqft', 'inventory_count',
                'days_on_market_avg', 'absorption_rate', 'new_listings',
                'sold_count', 'list_to_sale_ratio',
            ]

class LSTMForecaster(nn.Module):
    def __init__(self, n_features: int, config: LSTMConfig):
        super().__init__()
        self.config = config
        self.lstm = nn.LSTM(
            input_size=n_features, hidden_size=config.hidden_size,
            num_layers=config.num_layers,
            dropout=config.dropout if config.num_layers > 1 else 0.0,
            batch_first=True, bidirectional=config.bidirectional,
        )
        lstm_out = config.hidden_size * (2 if config.bidirectional else 1)
        self.head = nn.Sequential(
            nn.Dropout(config.dropout),
            nn.Linear(lstm_out, 64),
            nn.ReLU(),
            nn.Linear(64, len(config.forecast_horizons)),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :])

class LSTMTrainer:
    def __init__(self, config: Optional[LSTMConfig] = None):
        self.config = config or LSTMConfig()
        self.device = torch.device("cuba" if torch.cuda.is_available() else "cpu")
        self.model: Optional[LSTMForecaster] = None
        self.scaler: Optional[MinMaxScaler] = None
        self._metrics: dict = {}

    def _build_sequences(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        feat_cols = self.config.feature_cols
        lb = self.config.lookback_months
        horizons = self.config.forecast_horizons
        Xs, ys = [], []

        for zip_code, grp in df.groupby("zip_code"):
            grp = grp.sort_values("month")
            vals = grp[feat_cols].values
            med = grp["median_price"].values

            for i in range(lb, len(vals)):
                x_seq = vals[i - lb : i]
                targets, valid = [], True
                for h in horizons:
                    future_idx = i + h - 1
                    if future_idx >= len(med):
                        valid = False
                        break
                    targets.append((med[future_idx] - med[i - 1]) / (med[i - 1] + 1e-6))
                if valid:
                    Xs.append(x_seq)
                    ys.append(targets)

        return np.array(Xs, dtype=np.float32), np.array(ys, dtype=np.float32)

    def train(self, market_df: pd.DataFrame) -> dict:
        logger.info(f"LSTM training | rows={len(market_df)} | device={self.device}")
        feat_cols = self.config.feature_cols
        self.scaler = MinMaxScaler()
        market_df = market_df.copy()
        market_df[feat_cols] = self.scaler.fit_transform(market_df[feat_cols].fillna(0))

        X, y = self._build_sequences(market_df)
        if len(X) < 50:
            raise ValueError("Not enough sequential data (need 50+ sequences")

        split = int(len(X) * 0.85)
        Xtr = torch.tensor(X[:split]).to(self.device)
        ytr = torch.tensor(y[:split]).to(self.device)
        Xv = torch.tensor(X[split:]).to(self.device)
        yv = torch.tensor(y[split:]).to(self.device)

        self.model = LSTMForecaster(X.shape[2], self.config).to(self.device)
        optimizer = torch.optim.AdamW(self.model.parameters(),
                                      lr=self.config.lr,
                                      weight_decay=self.config.weight_decay)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, 5)
        loss_fn = nn.HuberLoss()
        loader = torch.utils.data.DataLoader(
            torch.utils.data.TensorDataset(Xtr, ytr), batch_size=self.config.batch_size, shuffle=True
        )

        best_val_loss, patience_cnt, best_state = float("inf"), 0, None

        for epoch in range(self.config.epochs):
            self.model.train()
            train_losses = []
            for xb, yb in loader:
                optimizer.zero_grad()
                loss = loss_fn(self.model(xb), yb)
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                train_losses.append(loss.item())

            self.model.eval()
            with torch.no_grad():
                val_loss = loss_fn(self.model(Xv), yv).item()
            scheduler.step(val_loss)

            if epoch % 10 == 0:
                logger.debug(f"Epoch {epoch:3d} | train={np.mean(train_losses):.4f} | val={val_loss:.4f}")

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.clone() for k, v in self.model.state_dict().items()}
                patience_cnt = 0
            else:
                patience_cnt += 1
                if patience_cnt >= self.config.patience:
                    logger.info(f"Early stopping at epoch {epoch}")
                    break

        if best_state:
            self.model.load_state_dict(best_state)

        self.model.eval()
        with torch.no_grad():
            val_pred_np = self.model(Xv).cpu().numpy()
            yv_np = yv.cpu().numpy()

        self._metrics = {
            f"mae_{h}mo": float(np.mean(np.abs(val_pred_np[:, i] - yv_np[:, i])))
            for i, h in enumerate(self.config.forecast_horizons)
        }
        logger.info(f"LSTM training done | {self._metrics}")
        return self._metrics

    def predict(self, sequence: np.ndarray) -> dict[str, float]:
        if self.model is None:
            raise RuntimeError("Model not trained or loaded")
        self.model.eval()
        x = torch.tensor(sequence[np.newaxis], dtype=torch.float32).to(self.device)
        with torch.no_grad():
            out = self.model(x).cpu().numpy()[0]
        return {f"{h}mo": float(out[i]) for i, h in enumerate(self.config.forecast_horizons)}

    def save(self, path: str):
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        torch.save(self.model.state_dict(), p / "lstm_weights.pt")
        joblib.dump(self.scaler, p / "scaler.joblib")
        cfg = {**self.config.__dict__}
        cfg.pop("feature_cols", None)
        with open(p / "config.json", "w") as f:
            json.dump({"config": cfg,
                       "metrics": self._metrics,
                       "n_features": len(self.config.feature_cols)},
                       f, indet=2)
        logger.info(f"LSTM saved to {p}")

    @classmethod
    def load(cls, path: str) -> "LSTMTrainer":
        p = Path(path)
        with open(p / "config.json") as f:
            meta = json.load(f)
        config = LSTMConfig(**meta["config"])
        trainer = cls(config)
        trainer.scaler = joblib.load(p / "scaler.joblib")
        trainer.model = LSTMForecaster(meta["n_features"], config)
        trainer.model.load_state_dict(torch.load(p / "lstm_weights.pt", map_location=trainer.device))
        trainer.model.to(trainer.device)
        trainer._metrics = meta["metrics"]
        logger.info(f"LSTM loaded from {p}")
        return trainer