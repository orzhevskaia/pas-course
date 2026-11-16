ALTER TABLE silver.sales_daily ADD COLUMN IF NOT EXISTS source_row_hash text;
-- Backfill existing facts by their original ingestion batch before subsequent corrections.
UPDATE silver.sales_daily s SET source_row_hash=b.row_hash
FROM bronze.sales b WHERE s.source_row_hash IS NULL AND s.store_id=b.store_id
AND s.date=b.business_date AND s.batch_id=b.batch_id;
-- Validate the existing million-row relation set-wise, avoiding a million per-row FK trigger lookups during backfill.
ALTER TABLE silver.sales_daily ADD CONSTRAINT sales_source_record_fk
 FOREIGN KEY(source_row_hash) REFERENCES bronze.sales(row_hash) NOT VALID;
ALTER TABLE silver.sales_daily VALIDATE CONSTRAINT sales_source_record_fk;
CREATE OR REPLACE VIEW gold.sales_enriched AS
 SELECT s.store_id,s.date,s.day_of_week,s.sales,s.customers,s.is_open,s.promo,s.state_holiday,s.school_holiday,s.batch_id,
 st.store_type, st.assortment, st.region, st.geography_kind,
 w.temperature,w.precipitation,v.version_id,v.attributes AS historical_attributes
 FROM silver.sales_daily s JOIN silver.stores st USING(store_id)
 LEFT JOIN silver.weather_daily w ON w.region=st.region AND w.date=s.date
 LEFT JOIN silver.store_versions v ON v.store_id=s.store_id AND s.date>=v.valid_from AND s.date<v.valid_to;
