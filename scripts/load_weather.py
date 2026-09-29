import requests
import psycopg2
import json
import time
import pandas as pd
from datetime import datetime

DB_PARAMS = {
    "dbname": "rossmann",
    "user": "rossmann",
    "password": "rossmann",
    "host": "localhost",
    "port": "5432"
}

# Период, за который у нас есть продажи в Rossmann
START_DATE = "2013-01-01"
END_DATE = "2015-12-31"

# Какие метео-параметры запрашиваем (ежедневные)
DAILY_PARAMS = [
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "rain_sum",
    "windspeed_10m_max"
]

def load_weather():
    print("🔄 Подключение к базе данных...")
    conn = psycopg2.connect(**DB_PARAMS)
    cursor = conn.cursor()
    
    # 1. Запись в журнал загрузок
    cursor.execute("""
        INSERT INTO meta.load_log (source_name, status, config_snapshot) 
        VALUES ('open_meteo_api', 'running', %s::jsonb)
        RETURNING load_id;
    """, (json.dumps({
        "start_date": START_DATE,
        "end_date": END_DATE,
        "params": DAILY_PARAMS
    }),))
    load_id = cursor.fetchone()[0]
    conn.commit()
    
    # 2. Загружаем справочник регионов (берем по одному магазину на регион)
    geo_df = pd.read_csv('/Users/macbookpro/pas/pas-course/data/store_geo_mapping.csv')
    regions = geo_df.drop_duplicates('region_name')[['region_name', 'latitude', 'longitude']].to_dict('records')
    
    print(f"🌍 Найдено уникальных регионов: {len(regions)}")
    print(f"📅 Период запроса: {START_DATE} → {END_DATE}")
    
    regions_loaded = 0
    regions_skipped = 0
    regions_failed = 0
    
    try:
        for i, region in enumerate(regions, 1):
            region_name = region['region_name']
            lat = region['latitude']
            lon = region['longitude']
            
            print(f"  [{i}/{len(regions)}] 🌤  Запрос погоды для {region_name} ({lat}, {lon})...", end=" ")
            
            # Проверка идемпотентности: если уже загружено — пропускаем
            cursor.execute("""
                SELECT COUNT(*) FROM bronze.raw_weather
                WHERE region_name = %s AND start_date = %s AND end_date = %s
            """, (region_name, START_DATE, END_DATE))
            
            if cursor.fetchone()[0] > 0:
                print("⏭  уже в базе, пропуск")
                regions_skipped += 1
                continue
            
            # Запрос к Open-Meteo Historical API
            url = "https://archive-api.open-meteo.com/v1/archive"
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": START_DATE,
                "end_date": END_DATE,
                "daily": ",".join(DAILY_PARAMS),
                "timezone": "Europe/Berlin"
            }
            
            # Retry-логика для устойчивости к сбоям (требование методички)
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = requests.get(url, params=params, timeout=30)
                    
                    # Обработка rate limit (429) и серверных ошибок (5xx)
                    if response.status_code == 429:
                        wait_time = 5 * (attempt + 1)
                        print(f"⏳ Rate limit, ждем {wait_time}с...", end=" ")
                        time.sleep(wait_time)
                        continue
                    elif response.status_code >= 500:
                        print(f"⚠️  Серверная ошибка {response.status_code}, retry...", end=" ")
                        time.sleep(2)
                        continue
                    
                    response.raise_for_status()
                    weather_data = response.json()
                    
                    # Вставляем в Bronze
                    cursor.execute("""
                        INSERT INTO bronze.raw_weather 
                        (region_name, latitude, longitude, start_date, end_date, payload)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (region_name, start_date, end_date) DO NOTHING
                    """, (
                        region_name, lat, lon, START_DATE, END_DATE,
                        json.dumps(weather_data)
                    ))
                    conn.commit()
                    
                    # Проверяем, что вернулись данные
                    days_count = len(weather_data.get('daily', {}).get('time', []))
                    print(f"✅ {days_count} дней")
                    regions_loaded += 1
                    break  # Успех — выходим из цикла retry
                    
                except requests.exceptions.Timeout:
                    print(f"⏱  Таймаут (попытка {attempt+1}/{max_retries})...", end=" ")
                    time.sleep(2)
                except requests.exceptions.RequestException as e:
                    print(f"❌ Ошибка сети: {e}")
                    break
            else:
                # Все попытки исчерпаны
                print("❌ Провал после всех попыток")
                regions_failed += 1
            
            # Пауза между запросами, чтобы не превысить rate limit Open-Meteo
            time.sleep(0.5)
        
        # 3. Успешное завершение в журнале
        cursor.execute("""
            UPDATE meta.load_log 
            SET status = 'success', end_time = NOW(), 
                rows_loaded = %s, rows_rejected = %s
            WHERE load_id = %s
        """, (regions_loaded, regions_failed, load_id))
        conn.commit()
        
        print(f"\n🎉 ГОТОВО!")
        print(f"   ✅ Загружено регионов: {regions_loaded}")
        print(f"   ⏭  Пропущено (уже были): {regions_skipped}")
        print(f"   ❌ Ошибок: {regions_failed}")
        
    except Exception as e:
        cursor.execute("""
            UPDATE meta.load_log 
            SET status = 'failed', end_time = NOW(), error_message = %s
            WHERE load_id = %s
        """, (str(e), load_id))
        conn.commit()
        print(f"\n❌ Критическая ошибка: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    load_weather()