-- Создание схем
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS meta;

-- Журнал загрузок
CREATE TABLE IF NOT EXISTS meta.load_log (
    load_id SERIAL PRIMARY KEY,
    source_name VARCHAR(100),
    start_time TIMESTAMP DEFAULT NOW(),
    end_time TIMESTAMP,
    status VARCHAR(20),
    rows_loaded INT,
    rows_rejected INT,
    error_message TEXT,
    config_snapshot JSONB
);

-- Bronze слой
CREATE TABLE IF NOT EXISTS bronze.raw_sales (
    id SERIAL PRIMARY KEY,
    loaded_at TIMESTAMP DEFAULT NOW(),
    payload JSONB,
    source_file VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS bronze.raw_stores (
    id SERIAL PRIMARY KEY,
    loaded_at TIMESTAMP DEFAULT NOW(),
    payload JSONB,
    source_file VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS bronze.raw_weather (
    id SERIAL PRIMARY KEY,
    loaded_at TIMESTAMP DEFAULT NOW(),
    region_name VARCHAR(100) NOT NULL,
    latitude NUMERIC(9, 6) NOT NULL,
    longitude NUMERIC(9, 6) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    payload JSONB NOT NULL,
    CONSTRAINT uq_weather_region_period UNIQUE (region_name, start_date, end_date)
);

-- Silver слой
CREATE TABLE IF NOT EXISTS silver.sales_daily (
    store_id INT NOT NULL,
    date DATE NOT NULL,
    day_of_week INT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    sales NUMERIC(12, 2) NOT NULL CHECK (sales >= 0),
    customers INT NOT NULL CHECK (customers >= 0),
    is_open BOOLEAN NOT NULL,
    is_promo BOOLEAN NOT NULL,
    state_holiday VARCHAR(10),
    school_holiday BOOLEAN,
    load_batch_id INT,
    loaded_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (store_id, date)
);

CREATE TABLE IF NOT EXISTS silver.weather_daily (
    region_name VARCHAR(100) NOT NULL,
    date DATE NOT NULL,
    temperature_mean NUMERIC(5, 2),
    temperature_max NUMERIC(5, 2),
    temperature_min NUMERIC(5, 2),
    precipitation_sum NUMERIC(7, 2) CHECK (precipitation_sum >= 0),
    rain_sum NUMERIC(7, 2) CHECK (rain_sum >= 0),
    windspeed_max NUMERIC(6, 2) CHECK (windspeed_max >= 0),
    load_batch_id INT,
    loaded_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (region_name, date)
);

CREATE TABLE IF NOT EXISTS silver.dim_regions (
    region_name VARCHAR(100) PRIMARY KEY,
    latitude NUMERIC(9, 6) NOT NULL,
    longitude NUMERIC(9, 6) NOT NULL,
    loaded_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS silver.dim_stores_scd (
    store_sk SERIAL PRIMARY KEY,
    store_id INT NOT NULL,
    store_type VARCHAR(10),
    assortment VARCHAR(10),
    competition_distance INT,
    competition_open_month INT,
    competition_open_year INT,
    promo2 BOOLEAN,
    promo2_since_week INT,
    promo2_since_year INT,
    valid_from DATE NOT NULL,
    valid_to DATE NOT NULL DEFAULT '9999-12-31',
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    change_reason VARCHAR(255),
    load_batch_id INT,
    loaded_at TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_stores_current 
    ON silver.dim_stores_scd(store_id) WHERE is_current = TRUE;

CREATE TABLE IF NOT EXISTS silver.store_region_mapping (
    store_id INT PRIMARY KEY,
    region_name VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS silver.features (
    date DATE NOT NULL,
    store_id INT NOT NULL,
    region_name VARCHAR(100),
    sales NUMERIC(12, 2),
    customers INT,
    is_open BOOLEAN,
    is_promo BOOLEAN,
    state_holiday VARCHAR(10),
    school_holiday BOOLEAN,
    store_type VARCHAR(10),
    assortment VARCHAR(10),
    has_competition BOOLEAN,
    competition_distance INT,
    is_promo2 BOOLEAN,
    temperature_mean NUMERIC(5, 2),
    precipitation_sum NUMERIC(7, 2),
    day_of_week INT,
    is_weekend BOOLEAN,
    day_of_year INT,
    load_batch_id INT,
    loaded_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (date, store_id)
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_sales_date ON silver.sales_daily(date);
CREATE INDEX IF NOT EXISTS idx_sales_store ON silver.sales_daily(store_id);
CREATE INDEX IF NOT EXISTS idx_weather_date ON silver.weather_daily(date);
CREATE INDEX IF NOT EXISTS idx_features_date ON silver.features(date);