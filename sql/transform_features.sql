DO $$
DECLARE
    v_load_id INT;
    v_rows_loaded INT;
BEGIN
    INSERT INTO meta.load_log (source_name, status, config_snapshot)
    VALUES ('transform_features_for_ml', 'running', 
            '{"operation": "join_sales_weather_stores"}'::jsonb)
    RETURNING load_id INTO v_load_id;
    
    INSERT INTO silver.features (
        date, store_id, region_name,
        sales, customers, is_open, is_promo, state_holiday, school_holiday,
        store_type, assortment, has_competition, competition_distance, is_promo2,
        temperature_mean, precipitation_sum,
        day_of_week, is_weekend, day_of_year,
        load_batch_id
    )
    SELECT 
        s.date,
        s.store_id,
        m.region_name,
        s.sales,
        s.customers,
        s.is_open,
        s.is_promo,
        s.state_holiday,
        s.school_holiday,
        st.store_type,
        st.assortment,
        CASE WHEN st.competition_distance IS NOT NULL THEN TRUE ELSE FALSE END AS has_competition,
        st.competition_distance,
        st.promo2 AS is_promo2,
        w.temperature_mean,
        w.precipitation_sum,
        EXTRACT(ISODOW FROM s.date)::INT AS day_of_week,
        CASE WHEN EXTRACT(ISODOW FROM s.date) IN (6, 7) THEN TRUE ELSE FALSE END AS is_weekend,
        EXTRACT(DOY FROM s.date)::INT AS day_of_year,
        v_load_id AS load_batch_id
    FROM silver.sales_daily s
    LEFT JOIN silver.store_region_mapping m ON s.store_id = m.store_id
    LEFT JOIN silver.dim_stores_scd st 
        ON s.store_id = st.store_id 
        AND st.is_current = TRUE
        AND s.date BETWEEN st.valid_from AND st.valid_to
    LEFT JOIN silver.weather_daily w 
        ON w.region_name = m.region_name 
        AND w.date = s.date
    ON CONFLICT (date, store_id) DO NOTHING;
    
    SELECT COUNT(*) INTO v_rows_loaded 
    FROM silver.features 
    WHERE load_batch_id = v_load_id;
    
    UPDATE meta.load_log 
    SET status = 'success', 
        end_time = NOW(), 
        rows_loaded = v_rows_loaded
    WHERE load_id = v_load_id;
    
    RAISE NOTICE '✅ Успех! Загружено строк в silver.features: %', v_rows_loaded;
    
EXCEPTION WHEN OTHERS THEN
    UPDATE meta.load_log 
    SET status = 'failed', 
        end_time = NOW(), 
        error_message = SQLERRM
    WHERE load_id = v_load_id;
    RAISE;
END $$;