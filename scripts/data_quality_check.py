"""
    PropIQ - Data Quality Gate (CI)
    Seeds a fresh database, then asserts the invariants that must ALWAYS
    hold. This is the automated version of scripts/audit.sql's tripwires -
    the informational sections (null-year rates, bedroom distribution) are
    intentionally omitted; only true correctness invariants are enforced.

    Note: CI can't ingest the 442k live OC parcels (network + 20min), so
    the OC-specific junk-APN invariant is covered by the fetcher unit test.
    Here we gate the seed corpus + referential integrity, which is what the
    ML layer actually trains on.

    Exit 0 if all invariants hold, 1 otherwise.
"""
import os, sys
from sqlalchemy import create_engine, text

DB = os.getenv("DATABASE_URL", "postgresql://propiq:propiq@localhost:5432/propiq")
fails = []

def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(label)

with create_engine(DB).connect() as c:
    q = lambda s: c.execute(text(s)).fetchone()

    seed, ready, stdev = q("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE last_sale_price>0 AND building_sqft>0
                            AND lot_size_sqft>0 AND bedrooms>0 AND bathrooms>0),
               COALESCE(STDDEV(estimated_value/NULLIF(last_sale_price,0)),0)
        FROM properties WHERE data_source='seed_synthetic'
    """)
    check("seed corpus present", seed >= 1000, f"{seed} rows")
    check("100% of seed rows AVM-ready", seed == ready, f"{ready}/{seed}")
    check("seed prices leakage-free (est/sale stdev > 0.03)", float(stdev) > 0.03,
          f"stdev={float(stdev):.4f}")

    nulls, = q("SELECT COUNT(*) FROM properties WHERE latitude IS NULL OR longitude IS NULL")
    check("no null coordinates", nulls == 0, f"{nulls} nulls")

    junk, = q("""SELECT COUNT(*) FROM properties
        WHERE TRIM(COALESCE(address,''))='' AND
              (parcel_number IS NULL OR TRIM(parcel_number)='' OR parcel_number ~ '^[0-]+$')""")
    check("no junk-APN blank-address rows", junk == 0, f"{junk} junk rows")

    of, oh, io = q("""SELECT
        (SELECT COUNT(*) FROM property_features pf LEFT JOIN properties p ON p.id=pf.property_id WHERE p.id IS NULL),
        (SELECT COUNT(*) FROM price_history ph LEFT JOIN properties p ON p.id=ph.property_id WHERE p.id IS NULL),
        (SELECT COUNT(*) FROM properties p LEFT JOIN users u ON u.id=p.owner_id WHERE u.id IS NULL)""")
    check("no orphaned features", of == 0)
    check("no orphaned price history", oh == 0)
    check("no properties with invalid owner", io == 0)

print()
if fails:
    print(f"DATA QUALITY GATE FAILED: {len(fails)} invariant(s) violated")
    sys.exit(1)
print("DATA QUALITY GATE PASSED: all invariants hold")