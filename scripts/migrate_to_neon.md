# Migrating PropIQ data to Neon (free tier)

Moves all ~474k parcels + seed corpus from local Docker Postgres into a free
Neon database, **excluding the `raw_data` JSON column** (122MB of re-processing
cruft the live app never reads). Result: ~118MB of data, comfortably inside
Neon's 512MB free tier.

Tables are copied in FK-dependency order (parents before children).

---

## 0. Prerequisites
- Neon project created, connection string in hand:
  `postgresql://USER:PASS@ep-xxx.REGION.aws.neon.tech/DBNAME?sslmode=require`
- Local stack running (`docker compose up -d`).

Set the Neon URL as a variable (PowerShell):
```powershell
$NEON = "postgresql://USER:PASS@ep-xxx.us-west-2.aws.neon.tech/DBNAME?sslmode=require"
```

## 1. Build the schema on Neon (via Alembic - same migrations as local)
```powershell
$env:DATABASE_URL = $NEON
alembic upgrade head
Remove-Item Env:DATABASE_URL
```

## 2. Export local data to CSVs (properties excludes raw_data)
```powershell
$PROP_COLS = "id,owner_id,address,city,state,zip_code,county,latitude,longitude,parcel_number,property_type,zoning,lot_size_sqft,building_sqft,year_built,bedrooms,bathrooms,stories,units,garage_spaces,pool,last_sale_price,last_sale_date,assessed_value,estimated_value,price_per_sqft,data_source,source_url,is_verified,created_at,updated_at"

docker exec propiq-db psql -U propiq -d propiq -c "\copy users TO '/tmp/users.csv' WITH CSV HEADER"
docker exec propiq-db psql -U propiq -d propiq -c "\copy (SELECT $PROP_COLS FROM properties) TO '/tmp/properties.csv' WITH CSV HEADER"
foreach ($t in @('neighborhoods','market_trends','scrape_jobs','property_features','price_history','property_valuations','projects','tasks','milestones')) {
    docker exec propiq-db psql -U propiq -d propiq -c "\copy $t TO '/tmp/$t.csv' WITH CSV HEADER"
}

New-Item -ItemType Directory -Force -Path .\migration_csv | Out-Null
foreach ($t in @('users','properties','neighborhoods','market_trends','scrape_jobs','property_features','price_history','property_valuations','projects','tasks','milestones')) {
    docker cp propiq-db:/tmp/$t.csv .\migration_csv\$t.csv
}
```

## 3. Load CSVs into Neon (parents before children)
```powershell
$ORDER = @('users','neighborhoods','scrape_jobs','market_trends',
           'properties',
           'property_features','price_history','property_valuations',
           'projects','tasks','milestones')

foreach ($t in $ORDER) {
    $cols = (Get-Content .\migration_csv\$t.csv -TotalCount 1)
    Write-Host "Loading $t ..."
    psql $NEON -c "\copy $t ($cols) FROM '.\migration_csv\$t.csv' WITH CSV HEADER"
}
```
> If `psql` isn't on PATH, use the full path to Postgres 16's `psql.exe`.

## 4. Reset sequences (so app inserts don't collide with copied IDs)
```powershell
psql $NEON -c "SELECT setval(pg_get_serial_sequence('properties','id'), (SELECT MAX(id) FROM properties));"
psql $NEON -c "SELECT setval(pg_get_serial_sequence('users','id'), (SELECT MAX(id) FROM users));"
psql $NEON -c "SELECT setval(pg_get_serial_sequence('projects','id'), COALESCE((SELECT MAX(id) FROM projects),1));"
```

## 5. Verify
```powershell
psql $NEON -c "SELECT data_source, COUNT(*) FROM properties GROUP BY data_source;"
# expect: oc_parcel_gis ~470689, seed_synthetic 4000
psql $NEON -c "SELECT pg_size_pretty(pg_database_size(current_database()));"
# expect: well under 512 MB
Get-Content scripts/audit.sql | psql $NEON
```

## 6. Clean up
```powershell
Remove-Item -Recurse .\migration_csv
docker exec propiq-db sh -c "rm -f /tmp/*.csv"
```

Point Render's `DATABASE_URL` at `$NEON` and the API serves live from Neon.