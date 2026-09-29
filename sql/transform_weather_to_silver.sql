DO $$
DECLARE
    v_load_id INT;
    v_rows_loaded INT;
BEGIN
    INSERT INTO meta.load_log (source_name, status, config_snapshot)
    VALUES ('transform_weather_to_silver', 'running', 
            '{"operation": "json_unpack_weather"}'::jsonb)
    RETURNING load_id INTO v_load_id;
    
    INSERT INTO silver.dim_regions (region_name, latitude, longitude)
    SELECT DISTINCT region_name, latitude, longitude
    FROM bronze.raw_weather
    ON CONFLICT (region_name) DO NOTHING;
    
    INSERT INTO silver.weather_daily (
        region_name, date, temperature_mean, temperature_max, temperature_min,
        precipitation_sum, rain_sum, windspeed_max, load_batch_id
    )
    SELECT 
        w.region_name,
        (d->>'time')::DATE AS date,
        NULLIF(d->>'temperature_2m_mean', 'null')::NUMERIC AS temperature_mean,
        NULLIF(d->>'temperature_2m_max', 'null')::NUMERIC AS temperature_max,
        NULLIF(d->>'temperature_2m_min', 'null')::NUMERIC AS temperature_min,
        NULLIF(d->>'precipitation_sum', 'null')::NUMERIC AS precipitation_sum,
        NULLIF(d->>'rain_sum', 'null')::NUMERIC AS rain_sum,
        NULLIF(d->>'windspeed_10m_max', 'null')::NUMERIC AS windspeed_max,
        v_load_id AS load_batch_id
    FROM bronze.raw_weather w,
         LATERAL jsonb_array_elements(
             (SELECT jsonb_agg(jsonb_build_object(
                 'time', t,
                 'temperature_2m_mean', tm,
                 'temperature_2m_max', tx,
                 'temperature_2m_min', tn,
                 'precipitation_sum', p,
                 'rain_sum', r,
                 'windspeed_10m_max', ws
             ))
             FROM 
                 jsonb_array_elements_text(w.payload->'daily'->'time') WITH ORDINALITY AS dates(t, ord)
                 LEFT JOIN jsonb_array_elements_text(w.payload->'daily'->'temperature_2m_mean') WITH ORDINALITY AS temps_mean(tm, ord2) ON dates.ord = temps_mean.ord2
                 LEFT JOIN jsonb_array_elements_text(w.payload->'daily'->'temperature_2m_max') WITH ORDINALITY AS temps_max(tx, ord3) ON dates.ord = temps_max.ord3
                 LEFT JOIN jsonb_array_elements_text(w.payload->'daily'->'temperature_2m_min') WITH ORDINALITY AS temps_min(tn, ord4) ON dates.ord = temps_min.ord4
                 LEFT JOIN jsonb_array_elements_text(w.payload->'daily'->'precipitation_sum') WITH ORDINALITY AS prec(p, ord5) ON dates.ord = prec.ord5
                 LEFT JOIN jsonb_array_elements_text(w.payload->'daily'->'rain_sum') WITH ORDINALITY AS rain(r, ord6) ON dates.ord = rain.ord6
                 LEFT JOIN jsonb_array_elements_text(w.payload->'daily'->'windspeed_10m_max') WITH ORDINALITY AS wind(ws, ord7) ON dates.ord = wind.ord7
             )
         ) AS d
    ON CONFLICT (region_name, date) DO NOTHING;
    
    SELECT COUNT(*) INTO v_rows_loaded 
    FROM silver.weather_daily 
    WHERE load_batch_id = v_load_id;
    
    UPDATE meta.load_log 
    SET status = 'success', 
        end_time = NOW(), 
        rows_loaded = v_rows_loaded
    WHERE load_id = v_load_id;
    
    RAISE NOTICE '✅ Успех! Загружено строк в silver.weather_daily: %', v_rows_loaded;
    
EXCEPTION WHEN OTHERS THEN
    UPDATE meta.load_log 
    SET status = 'failed', 
        end_time = NOW(), 
        error_message = SQLERRM
    WHERE load_id = v_load_id;
    RAISE;
END $$;