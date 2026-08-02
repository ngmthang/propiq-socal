# PropIQ — SoCal Real Estate Investment Intelligence

[![CI](https://github.com/ngmthang/propiq-socal/actions/workflows/ci.yml/badge.svg)](https://github.com/ngmthang/propiq-socal/actions/workflows/ci.yml)

A full-stack platform for analyzing Southern California real estate: **442,000+ real
Orange County parcels** ingested from public county GIS, ML-powered valuations with
SHAP explainability, LSTM market forecasting, and Claude-powered deal analysis —
behind a FastAPI backend and a React dashboard.

Built as a solo project to learn what production data/ML engineering actually
involves: real public-records ingestion, spatial joins, target-leakage debugging,
model observability, migrations, and CI.

## Architecture

Four layers, each independently testable:

┌─ Layer 4: Frontend ─────────── React + Vite · Tailwind · Mapbox · D3 · dnd-kit Kanban
├─ Layer 3: API ─────────────── FastAPI · JWT auth · rate limiting · Sentry · admin observability
├─ Layer 2: ML/AI ───────────── XGBoost AVM + SHAP · LSTM forecaster (PyTorch) · Claude deal analysis
└─ Layer 1: Data ────────────── PostgreSQL/SQLAlchemy · county GIS ingestion · Alembic · APScheduler


## The data (and what's real vs. synthetic)

- **Real:** 442k Orange County parcels from OC Public Works' public ArcGIS layers —
  APNs, site addresses, coordinates, year built, bedrooms, per-parcel unit counts
  (derived from shared-APN analysis of condo/townhome developments), and county
  zoning where published. Zip assignment is done by point-in-polygon against US
  Census TIGERweb ZCTA boundaries; zoning by spatial join against the county
  zoning layer, with `UNKNOWN` where the data honestly doesn't exist (city-zoned
  parcels aren't in the county layer).
- **Synthetic:** sale prices and valuations. California does not publicly disclose
  real transaction prices, so ML training uses a generated corpus of ~4,000 sold
  properties whose prices derive from a hidden `intrinsic_value` — with sale price
  and estimated value drawn **independently**, never from each other.

That independence matters: an earlier version derived `estimated_value` from
`sale_price`, and the AVM "achieved" R² = 0.987 by learning the leak instead of
the housing market. The rebuilt generator produces an honest R² ≈ 0.97, and CI
now enforces an **R² upper bound as a leakage tripwire** — a suspiciously perfect
model fails the build.

## ML

| Model | Stack | Metrics (validation) |
|---|---|---|
| AVM (valuation) | XGBoost + SHAP | R² 0.978 · MAPE 6.5% · MAE $119k |
| Market forecaster | 2-layer LSTM (PyTorch) | MAE 2.8% / 3.7% / 3.8% on 3/6/12-mo price change |

Properties missing core physical data (most county parcel records) are **refused
valuation** with an HTTP 422 naming the missing fields, rather than silently
valued off imputation defaults — enforced at the model boundary
(`Property.is_avm_ready`), the inference engine, and the training query.

## Running it

```bash
docker compose up -d --build          # db + api + worker
alembic upgrade head                  # schema (or stamp head on an existing db)
python -m data_layer.seeds.seed_avm_data          # 4k sold properties
python -m data_layer.seeds.seed_market_history    # 60 months of market history
python -m data_layer.scrapers.ingest_oc_parcels   # 442k real OC parcels (~20 min)
docker exec -e MODEL_DIR=/app/models/saved propiq-api \
  python -m ml_layer.training.scheduler --job avm
docker exec -e MODEL_DIR=/app/models/saved propiq-api \
  python -m ml_layer.training.scheduler --job lstm
docker compose restart api
cd frontend && npm ci && npm run dev  # http://localhost:5173
```

## CI

Four jobs on every push (`.github/workflows/ci.yml`):

1. **Backend smoke** — compiles every module, instantiates the full scraper
   pipeline, asserts all routers are registered via the OpenAPI schema
2. **Seed + train integration** — seeds a real Postgres service container,
   trains the AVM end-to-end, asserts artifacts land where the API reads them,
   and enforces the leakage tripwire (0.80 < R² < 0.998)
3. **Frontend build** — Vite production build
4. **Migration drift check** — upgrades an empty DB to head, then asserts an
   autogenerate diff against the models comes back empty

Every assertion encodes a bug that actually shipped once.

## Disclaimer

Demo data — property values shown are synthetic, not real transactions.
Estimates are AI-generated model outputs, not appraisals; do not use them to
make financial decisions.