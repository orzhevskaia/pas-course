DO $$
DECLARE
    v_load_id INT;
    v_rows_loaded INT;
BEGIN
    INSERT INTO meta.load_log (source_name, status, config_snapshot)
    VALUES ('transform_sales_to_silver', 'running', 
            '{"operation": "bronze_to_silver_sales", "deduplication": true, "dq_filters": true}'::jsonb)
    RETURNING load_id INTO v_load_id;
    
    INSERT INTO silver.sales_daily (
        store_id, date, day_of_week, sales, customers, 
        is_open, is_promo, state_holiday, school_holiday, load_batch_id
    )
    SELECT DISTINCT ON (store_id, date)
        (payload->>'Store')::INT AS store_id,
        (payload->>'Date')::DATE AS date,
        (payload->>'DayOfWeek')::INT AS day_of_week,
        (payload->>'Sales')::NUMERIC AS sales,
        (payload->>'Customers')::INT AS customers,
        CASE WHEN (payload->>'Open')::INT = 1 THEN TRUE ELSE FALSE END AS is_open,
        CASE WHEN (payload->>'Promo')::INT = 1 THEN TRUE ELSE FALSE END AS is_promo,
        payload->>'StateHoliday' AS state_holiday,
        CASE WHEN (payload->>'SchoolHoliday')::INT = 1 THEN TRUE ELSE FALSE END AS school_holiday,
        v_load_id AS load_batch_id
    FROM bronze.raw_sales
    WHERE 
        (payload->>'Store')::INT IS NOT NULL
        AND (payload->>'Date')::DATE IS NOT NULL
        AND (payload->>'Sales')::NUMERIC >= 0
        AND (payload->>'Customers')::INT >= 0
        AND (payload->>'DayOfWeek')::INT BETWEEN 1 AND 7
        AND NOT ((payload->>'Open')::INT = 1 AND (payload->>'Sales')::NUMERIC = 0)
    ON CONFLICT (store_id, date) DO NOTHING;
    
    SELECT COUNT(*) INTO v_rows_loaded 
    FROM silver.sales_daily 
    WHERE load_batch_id = v_load_id;
    
    UPDATE meta.load_log 
    SET status = 'success', 
        end_time = NOW(), 
        rows_loaded = v_rows_loaded
    WHERE load_id = v_load_id;
    
    RAISE NOTICE '✅ Успех! Загружено строк в silver.sales_daily: %', v_rows_loaded;
    
EXCEPTION WHEN OTHERS THEN
    UPDATE meta.load_log 
    SET status = 'failed', 
        end_time = NOW(), 
        error_message = SQLERRM
    WHERE load_id = v_load_id;
    RAISE;
END $$;