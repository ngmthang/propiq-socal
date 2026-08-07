# PropIQ — SoCal Real Estate Investment Intelligence

[![CI](https://github.com/ngmthang/propiq-socal/actions/workflows/ci.yml/badge.svg)](https://github.com/ngmthang/propiq-socal/actions/workflows/ci.yml)

**🔗 Live demo: [propiqsocal.vercel.app](https://propiqsocal.vercel.app)**
*(Free-tier hosted — the API sleeps when idle, so the first request after a pause takes ~30–50s to wake. Deployed across Vercel + Render + Neon.)*

A full-stack platform for analyzing Southern California real estate: **474,689 real
Orange County parcels** ingested from public county GIS, real market prices
ingested from live listings, ML-powered valuations with SHAP explainability, an
LSTM market forecaster, and a county-scale interactive map — behind a FastAPI
backend and a React dashboard.

Built as a solo project to learn what production data/ML engineering actually
involves: real public-records ingestion at scale, third-party data enrichment
under real constraints (rate limits, bot-detection, licensing), target-leakage
debugging, honest model boundaries, and keeping a system correct as it grows two
orders of magnitude past its original design point.

## Architecture

Four layers, each independently testable:

┌─ Layer 4: Frontend ─────────── React + Vite · Tailwind · Mapbox GL (clustered) · D3 · dnd-kit Kanban
├─ Layer 3: API ─────────────── FastAPI · JWT auth · rate limiting · Sentry · admin observability
├─ Layer 2: ML/AI ───────────── XGBoost AVM + SHAP · LSTM forecaster (PyTorch) · Claude deal analysis
└─ Layer 1: Data ────────────── PostgreSQL/SQLAlchemy · county GIS + Zillow ingestion · Alembic · APScheduler


## The data — what's real, what's synthetic, and where the line is

This is the part of the project I'd point an interviewer to first: every piece
of price data in the system is labeled by provenance, and the app enforces a
strict hierarchy rather than ever blending real and synthetic numbers into one
ambiguous figure.

**Real (474,689 parcels):**
- APNs, site addresses, coordinates, year built, bedrooms, and county zoning —
  from Orange County Public Works' public ArcGIS parcel layer. Zip assignment
  is point-in-polygon against US Census TIGERweb ZCTA boundaries. Ingestion is
  verified for **completeness**, not just correctness: a reconciliation script
  checks stored counts against the county's own polygon counts, which once
  caught a blank-address skip silently dropping ~28,600 valid parcels — a
  defect invisible to every correctness check, since the data that *was*
  stored was accurate.
- **Real prices for 6,115 of those parcels**, ingested from live Zillow
  listings via RapidAPI (a licensed gateway, not a scrape) and matched to
  existing parcels by a conservative, exact-match address normalizer — house
  number + street only, city/unit suffixes stripped, zero fuzzy matching.
  A wrong match would silently attach one property's real price to a
  different parcel, which is worse than leaving it unpriced, so ambiguous
  matches are skipped rather than guessed. (Redfin was the first attempt —
  its CSV export endpoint is unauthenticated and looked promising, but is
  blocked outright by CloudFront at the TLS-fingerprint level in production,
  confirmed identically from inside and outside Docker. Pivoted to a licensed
  API instead of trying to defeat bot detection.) This same enrichment pass
  backfills square footage / beds / baths for matched parcels too, which is
  what makes them AVM-eligible in the first place — OC's public parcel layer
  doesn't expose those fields at all.

**Synthetic, and disclosed as such everywhere in the UI:**
- ~4,000 sold properties for AVM training. California doesn't publicly
  disclose real transaction prices, so training uses a generated corpus whose
  prices derive from a hidden `intrinsic_value`, with sale price and
  estimated value drawn **independently** — never from each other. That
  independence matters: an earlier version derived `estimated_value` from
  `sale_price` directly, and the AVM "achieved" R² = 0.987 by learning the
  leak instead of the housing market. The rebuilt generator produces an
  honest R² ≈ 0.98, and CI enforces an **R² upper bound as a leakage
  tripwire** — a suspiciously perfect model fails the build.
- Neighborhood context (walk/transit/school scores) and LSTM market history,
  extended to every real OC zip **that has a real price to anchor to** — 55
  of ~57 OC zips as of the last run. The two zips with zero real listings
  matched are left with no forecast rather than seeded off a made-up number;
  price-adjacent synthetic data is never fabricated, full stop, even to fill
  a gap.

**The price-display hierarchy, enforced identically on the map, the list, and
every property page:** real active listing → real last sale → AVM estimate →
honestly "unavailable." A property either shows a real number, a clearly
labeled model estimate, or a plain statement that neither exists — never an
unlabeled blend of the two.

## ML

| Model | Stack | Metrics (validation) |
|---|---|---|
| AVM (valuation) | XGBoost + SHAP | R² 0.979 · MAPE 6.43% · MAE $117k |
| Market forecaster | 2-layer LSTM (PyTorch) | MAE 2.96% / 3.72% / 4.00% on 3/6/12-mo price change |

Properties missing core physical data are **refused valuation** with an HTTP
422 naming the missing fields, rather than silently valued off imputation
defaults — enforced at the model boundary (`Property.is_avm_ready`), the
inference engine, and the training/scoring queries alike. The same 422, not a
500, propagates through the full-analysis endpoint too — a property with
insufficient data is an expected outcome, not a server fault.

Batch scoring (`ml_layer/training/batch_score.py`) applies the trained AVM to
every eligible property in one vectorized pass — building the full feature
matrix and calling XGBoost once, rather than looping per-row through the
request-serving path (which also computes SHAP, far too slow at scale for a
job that just needs a number, not an explanation).

## Serving 474k points on a map without melting the browser

The map doesn't render one DOM marker per property — at this scale that's a
non-starter. Points feed into a clustered Mapbox GL GeoJSON source instead, so
the WebGL layer draws them, and a dedicated `/api/search/map` endpoint returns
only whatever's inside the current viewport (bbox-scoped, capped, with request
cancellation so a slow response for a viewport you've since panned away from
can never land after a fresher one and show stale pins).

## Running it

```bash
docker compose up -d --build          # db + api + worker
alembic upgrade head                  # schema (or stamp head on an existing db)
python -m data_layer.seeds.seed_avm_data              # 4k sold properties
python -m data_layer.seeds.seed_market_history        # 60 months of market history
python -m data_layer.scrapers.ingest_oc_parcels       # 474k real OC parcels (~20 min)
python -m data_layer.scrapers.enrich_from_zillow      # real prices + specs, address-matched
python -m data_layer.seeds.extend_synthetic_coverage  # context + forecast for every real-priced zip
docker exec -e MODEL_DIR=/app/models/saved propiq-api \
  python -m ml_layer.training.scheduler --job avm
docker exec -e MODEL_DIR=/app/models/saved propiq-api \
  python -m ml_layer.training.scheduler --job lstm
docker exec propiq-api python -m ml_layer.training.batch_score
docker compose restart api
cd frontend && npm ci && npm run dev  # http://localhost:5173
```

## Deployment

Deployed 100% free, with an architecture that scales by changing plans, not code:

- **Frontend → Vercel** (static Vite build, CDN)
- **API → Render** (Docker, free tier). The serving image is **torch-free**: the
  request-serving process loads only the XGBoost AVM, never PyTorch, so it fits in
  512MB and can run as N identical stateless replicas. `SERVING_ONLY=true` enforces
  the boundary — the LSTM/training code and its heavy deps are never imported.
- **Postgres → Neon** (serverless, free tier). All 474k parcels fit comfortably by
  excluding the re-processing `raw_data` JSON blob at migration time.

Training and serving are separate lifecycles (the `docker-compose` split was
designed that way from day one): the API serves precomputed model artifacts; the
worker owns retraining. Scaling to real traffic is a host/plan upgrade, not a
rewrite — the stateless-API boundary is the load-bearing decision that makes that
true.

> **Note:** the real-price enrichment, LSTM fixes, batch scoring, and coverage
> extension described above were built and verified against a local Docker
> stack. If the live demo link looks like it's missing any of this, it means
> those changes haven't been deployed to Render/Neon yet — redeploy to bring
> it current.

## CI

Six jobs on every push (`.github/workflows/ci.yml`):

1. **Backend smoke** — compiles every module, instantiates the full scraper
   pipeline, asserts all routers are registered via the OpenAPI schema
2. **Seed + train integration** — seeds a real Postgres service container,
   trains the AVM end-to-end, asserts artifacts land where the API reads them,
   and enforces the leakage tripwire (0.80 < R² < 0.998)
3. **Frontend build** — Vite production build
4. **Migration drift check** — upgrades an empty DB to head, then asserts an
   autogenerate diff against the models comes back empty
5. **Data quality gate** — seeds a real Postgres, asserts the invariants that must
   always hold (leakage-free seed corpus, no junk-APN rows, referential integrity)
6. **Unit tests** — pytest suite; notably a regression test for the blank-address
   parcel-loss bug, so it can never silently return

Every assertion encodes a bug that actually shipped once.

## Disclaimer

Demo data — property values shown may be synthetic and are not real
transactions except where explicitly sourced from a live listing. Estimates
are AI-generated model outputs, not appraisals; do not use them to make
financial decisions.