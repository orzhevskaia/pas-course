CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE TABLE meta.runs (
 run_id uuid PRIMARY KEY, stage text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
 ended_at timestamptz, status text NOT NULL CHECK(status IN ('running','success','failed')),
 logical_date date NOT NULL, config jsonb NOT NULL DEFAULT '{}', stats jsonb NOT NULL DEFAULT '{}', error text
);
CREATE TABLE meta.batches (
 batch_id bigserial PRIMARY KEY, source text NOT NULL, partition_key text NOT NULL,
 checksum text NOT NULL, run_id uuid NOT NULL REFERENCES meta.runs,
 rows_count integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(source,partition_key,checksum)
);
CREATE TABLE bronze.sales (
 row_hash text PRIMARY KEY, store_id int NOT NULL, business_date date NOT NULL,
 payload jsonb NOT NULL, batch_id bigint NOT NULL REFERENCES meta.batches
);
CREATE INDEX ON bronze.sales(business_date);
CREATE TABLE bronze.stores (
 row_hash text PRIMARY KEY, store_id int NOT NULL, payload jsonb NOT NULL,
 batch_id bigint NOT NULL REFERENCES meta.batches
);
CREATE TABLE bronze.weather (
 row_hash text PRIMARY KEY, region text NOT NULL, business_date date NOT NULL,
 payload jsonb NOT NULL, batch_id bigint NOT NULL REFERENCES meta.batches
);
CREATE TABLE silver.stores (
 store_id int PRIMARY KEY, store_type text NOT NULL, assortment text NOT NULL,
 competition_distance double precision, competition_month int, competition_year int,
 promo2 boolean, promo_week int, promo_year int, promo_interval text,
 region text NOT NULL, latitude double precision NOT NULL, longitude double precision NOT NULL,
 geography_kind text NOT NULL DEFAULT 'synthetic', batch_id bigint REFERENCES meta.batches
);
CREATE TABLE silver.sales_daily (
 store_id int REFERENCES silver.stores, date date NOT NULL, day_of_week smallint NOT NULL CHECK(day_of_week BETWEEN 1 AND 7),
 sales double precision NOT NULL CHECK(sales>=0), customers int NOT NULL CHECK(customers>=0),
 is_open boolean NOT NULL, promo boolean NOT NULL, state_holiday text NOT NULL, school_holiday boolean NOT NULL,
 batch_id bigint NOT NULL REFERENCES meta.batches, PRIMARY KEY(store_id,date)
);
CREATE INDEX ON silver.sales_daily(date);
CREATE TABLE silver.weather_daily (
 region text NOT NULL, date date NOT NULL, temperature double precision, precipitation double precision CHECK(precipitation>=0),
 batch_id bigint NOT NULL REFERENCES meta.batches, PRIMARY KEY(region,date)
);
CREATE TABLE silver.store_versions (
 version_id bigserial PRIMARY KEY, store_id int NOT NULL REFERENCES silver.stores,
 attributes jsonb NOT NULL, attribute_hash text NOT NULL,
 valid_from date NOT NULL, valid_to date NOT NULL DEFAULT '9999-12-31',
 recorded_at timestamptz NOT NULL DEFAULT now(), run_id uuid NOT NULL REFERENCES meta.runs,
 reason text NOT NULL, CHECK(valid_from<valid_to), UNIQUE(store_id,valid_from)
);
CREATE UNIQUE INDEX ON silver.store_versions(store_id) WHERE valid_to='9999-12-31';
CREATE TABLE meta.dq_results (
 id bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES meta.runs, layer text NOT NULL, rule text NOT NULL,
 kind text NOT NULL, severity text NOT NULL, failed_count int NOT NULL, checked_count int NOT NULL,
 passed boolean NOT NULL, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE meta.quarantine (
 id bigserial PRIMARY KEY, source text NOT NULL, record_hash text NOT NULL, payload jsonb NOT NULL,
 reason text NOT NULL, run_id uuid NOT NULL REFERENCES meta.runs, created_at timestamptz DEFAULT now(),
 UNIQUE(source,record_hash,reason)
);
CREATE TABLE meta.assets (
 name text PRIMARY KEY, kind text NOT NULL, description text NOT NULL, grain text, owner_role text NOT NULL,
 refresh_policy text NOT NULL, definition jsonb NOT NULL
);
CREATE TABLE meta.lineage (
 upstream text NOT NULL REFERENCES meta.assets, downstream text NOT NULL REFERENCES meta.assets,
 operation text NOT NULL, PRIMARY KEY(upstream,downstream)
);
CREATE TABLE meta.model_runs (
 model_run_id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES meta.runs, origin date NOT NULL,
 data_hash text NOT NULL, code_hash text NOT NULL, artifact_path text NOT NULL,
 config jsonb NOT NULL, environment jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE gold.forecasts (
 model_run_id uuid NOT NULL REFERENCES meta.model_runs, model text NOT NULL, origin date NOT NULL,
 store_id int NOT NULL REFERENCES silver.stores, date date NOT NULL, prediction double precision NOT NULL CHECK(prediction>=0),
 schedule_source text NOT NULL, PRIMARY KEY(model_run_id,model,store_id,date), CHECK(date>origin)
);
CREATE TABLE gold.validation_predictions (
 model_run_id uuid NOT NULL REFERENCES meta.model_runs, model text NOT NULL, origin date NOT NULL,
 store_id int NOT NULL REFERENCES silver.stores, date date NOT NULL, actual double precision NOT NULL,
 prediction double precision NOT NULL, PRIMARY KEY(model_run_id,model,origin,store_id,date)
);
CREATE TABLE gold.metrics (
 model_run_id uuid NOT NULL REFERENCES meta.model_runs, model text NOT NULL, origin date NOT NULL,
 segment text NOT NULL, mae double precision, wape double precision, rmspe double precision, n int NOT NULL,
 PRIMARY KEY(model_run_id,model,origin,segment)
);
CREATE VIEW gold.sales_enriched AS
 SELECT s.*, st.store_type, st.assortment, st.region, st.geography_kind,
 w.temperature,w.precipitation,v.version_id,v.attributes AS historical_attributes
 FROM silver.sales_daily s JOIN silver.stores st USING(store_id)
 LEFT JOIN silver.weather_daily w ON w.region=st.region AND w.date=s.date
 LEFT JOIN silver.store_versions v ON v.store_id=s.store_id AND s.date>=v.valid_from AND s.date<v.valid_to;
