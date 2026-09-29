DO $$
DECLARE
    v_load_id INT;
    v_rows_loaded INT;
BEGIN
    INSERT INTO meta.load_log (source_name, status, config_snapshot)
    VALUES ('transform_stores_scd2', 'running', 
            '{"operation": "scd_type_2_stores", "strategy": "initial_load"}'::jsonb)
    RETURNING load_id INTO v_load_id;
    
    INSERT INTO silver.dim_stores_scd (
        store_id, store_type, assortment, competition_distance,
        competition_open_month, competition_open_year,
        promo2, promo2_since_week, promo2_since_year,
        valid_from, valid_to, is_current, change_reason, load_batch_id
    )
    SELECT DISTINCT ON ((payload->>'Store')::NUMERIC::INT)
        (payload->>'Store')::NUMERIC::INT AS store_id,
        payload->>'StoreType' AS store_type,
        payload->>'Assortment' AS assortment,
        NULLIF(payload->>'CompetitionDistance', '')::NUMERIC::INT AS competition_distance,
        NULLIF(payload->>'CompetitionOpenSinceMonth', '')::NUMERIC::INT AS competition_open_month,
        NULLIF(payload->>'CompetitionOpenSinceYear', '')::NUMERIC::INT AS competition_open_year,
        CASE WHEN payload->>'Promo2' = '1' THEN TRUE ELSE FALSE END AS promo2,
        NULLIF(payload->>'Promo2SinceWeek', '')::NUMERIC::INT AS promo2_since_week,
        NULLIF(payload->>'Promo2SinceYear', '')::NUMERIC::INT AS promo2_since_year,
        COALESCE(
            MAKE_DATE(
                NULLIF(payload->>'CompetitionOpenSinceYear', '')::NUMERIC::INT,
                NULLIF(payload->>'CompetitionOpenSinceMonth', '')::NUMERIC::INT,
                1
            ),
            '2013-01-01'::DATE
        ) AS valid_from,
        '9999-12-31'::DATE AS valid_to,
        TRUE AS is_current,
        'Initial load' AS change_reason,
        v_load_id AS load_batch_id
    FROM bronze.raw_stores
    WHERE (payload->>'Store')::NUMERIC::INT IS NOT NULL
    ON CONFLICT DO NOTHING;
    
    SELECT COUNT(*) INTO v_rows_loaded 
    FROM silver.dim_stores_scd 
    WHERE load_batch_id = v_load_id;
    
    UPDATE meta.load_log 
    SET status = 'success', 
        end_time = NOW(), 
        rows_loaded = v_rows_loaded
    WHERE load_id = v_load_id;
    
    RAISE NOTICE '✅ Успех! Загружено записей в silver.dim_stores_scd: %', v_rows_loaded;
    
EXCEPTION WHEN OTHERS THEN
    UPDATE meta.load_log 
    SET status = 'failed', 
        end_time = NOW(), 
        error_message = SQLERRM
    WHERE load_id = v_load_id;
    RAISE;
END $$;