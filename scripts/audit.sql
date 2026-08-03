-- PropIQ Data Quality Audit
-- Run: Get-Content scripts/audit.sql | docker exec -i propiq-db psql -U propiq -d propiq --pset pager=off

\echo '=== 1. Row counts by source (expected: ~442k oc_parcel_gis, 4000 seed_synthetic) ==='
SELECT data_source, COUNT(*) FROM properties GROUP BY data_source ORDER BY count DESC;

\echo '=== 2. Blank addresses: junk (invalid APN) vs honest (real parcel, county left address blank) ==='
SELECT
  COUNT(*) FILTER (WHERE parcel_number IS NULL OR TRIM(parcel_number) = '' OR parcel_number ~ '^[0-]+$') AS junk_blank,   -- expected 0
  COUNT(*) FILTER (WHERE parcel_number IS NOT NULL AND parcel_number !~ '^[0-]+$') AS honest_blank        -- expected: some, fine
FROM properties
WHERE data_source = 'oc_parcel_gis' AND TRIM(COALESCE(address, '')) = '';

\echo '=== 3. Coordinates outside Orange County bounding box, OC rows only (expected: 0) ==='
SELECT COUNT(*) AS out_of_bounds FROM properties
WHERE data_source = 'oc_parcel_gis'
  AND (latitude  NOT BETWEEN 33.33 AND 33.96
   OR  longitude NOT BETWEEN -118.15 AND -117.40);

\echo '=== 4. NULL coordinates anywhere (expected: 0 - column is NOT NULL, this is a tripwire) ==='
SELECT COUNT(*) AS null_coords FROM properties WHERE latitude IS NULL OR longitude IS NULL;

\echo '=== 5. year_built sanity for OC rows (nulls are honest; values must be plausible) ==='
SELECT
  COUNT(*) FILTER (WHERE year_built IS NULL)                    AS null_year,
  ROUND(100.0 * COUNT(*) FILTER (WHERE year_built IS NULL) / NULLIF(COUNT(*), 0), 1) AS null_pct,
  COUNT(*) FILTER (WHERE year_built < 1870 OR year_built > 2026) AS implausible_year,
  MIN(year_built) FILTER (WHERE year_built >= 1870)             AS oldest,
  MAX(year_built)                                               AS newest
FROM properties WHERE data_source = 'oc_parcel_gis';

\echo '=== 6. Bedrooms distribution for OC rows (bedrooms=0 is a known real artifact: non-residential/unrecorded) ==='
SELECT bedrooms, COUNT(*) FROM properties
WHERE data_source = 'oc_parcel_gis'
GROUP BY bedrooms ORDER BY bedrooms NULLS LAST LIMIT 15;

\echo '=== 7. Units: top multi-unit parcels (spot-check a few against real listings) ==='
SELECT parcel_number, units, address, city FROM properties
WHERE data_source = 'oc_parcel_gis' ORDER BY units DESC LIMIT 8;

\echo '=== 8. Zip/city pairs (each zip must map to exactly ONE city; count should be ~57) ==='
SELECT COUNT(*) AS zip_city_pairs,
       COUNT(DISTINCT zip_code) AS distinct_zips,
       COUNT(*) - COUNT(DISTINCT zip_code) AS zips_with_multiple_cities  -- expected 0
FROM (SELECT DISTINCT zip_code, city FROM properties WHERE data_source = 'oc_parcel_gis') t;

\echo '=== 9. Zoning distribution, OC rows (UNKNOWN dominates honestly; real codes only near unincorporated land) ==='
SELECT zoning, COUNT(*) FROM properties WHERE data_source = 'oc_parcel_gis'
GROUP BY zoning ORDER BY count DESC;

\echo '=== 10. Orphaned children (expected: all 0) ==='
SELECT
  (SELECT COUNT(*) FROM property_features pf LEFT JOIN properties p ON p.id = pf.property_id WHERE p.id IS NULL) AS orphan_features,
  (SELECT COUNT(*) FROM price_history ph LEFT JOIN properties p ON p.id = ph.property_id WHERE p.id IS NULL)     AS orphan_history,
  (SELECT COUNT(*) FROM properties p LEFT JOIN users u ON u.id = p.owner_id WHERE u.id IS NULL)                  AS invalid_owner;

\echo '=== 11. Seed corpus health (expected: 4000 rows, 100% AVM-ready, ratio stdev > 0.05 = leakage-free) ==='
SELECT
  COUNT(*) AS seed_rows,
  COUNT(*) FILTER (WHERE last_sale_price > 0 AND building_sqft > 0 AND lot_size_sqft > 0
                     AND bedrooms > 0 AND bathrooms > 0) AS avm_ready,
  ROUND(AVG(estimated_value / NULLIF(last_sale_price, 0))::numeric, 4) AS est_over_sale_mean,
  ROUND(STDDEV(estimated_value / NULLIF(last_sale_price, 0))::numeric, 4) AS est_over_sale_stdev
FROM properties WHERE data_source = 'seed_synthetic';

\echo '=== 12. Market history coverage (expected: 60 months for the 20 seed zips) ==='
SELECT COUNT(DISTINCT zip_code) AS zips,
       MIN(snapshot_date)::date AS earliest,
       MAX(snapshot_date)::date AS latest,
       COUNT(*) AS snapshots
FROM market_trends;

\echo '=== 13. ScrapeJob ledger vs reality (saved+updated of last full run should ~match OC row count) ==='
SELECT id, source, status, records_fetched, records_saved, records_updated, records_skipped,
       ROUND(duration_secs::numeric, 0) AS secs, completed_at::date
FROM scrape_jobs ORDER BY id DESC LIMIT 5;

\echo '=== 14. Duplicate (address, zip) within OC rows (some legit: units sharing an address-less parcel; large counts merit a look) ==='
SELECT COUNT(*) AS addr_zip_duplicate_groups FROM (
  SELECT address, zip_code FROM properties
  WHERE data_source = 'oc_parcel_gis'
  GROUP BY address, zip_code HAVING COUNT(*) > 1
) d;