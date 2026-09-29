import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

DB_PARAMS = {
    "dbname": "rossmann",
    "user": "rossmann",
    "password": "rossmann",
    "host": "localhost",
    "port": "5432"
}

def load_mapping():
    conn = psycopg2.connect(**DB_PARAMS)
    cursor = conn.cursor()
    
    geo_df = pd.read_csv('/Users/macbookpro/pas/pas-course/data/store_geo_mapping.csv')
    
    values = [(int(row['store_id']), row['region_name']) for _, row in geo_df.iterrows()]
    
    execute_values(cursor, """
        INSERT INTO silver.store_region_mapping (store_id, region_name)
        VALUES %s
        ON CONFLICT (store_id) DO NOTHING
    """, values)
    
    conn.commit()
    print(f"✅ Загружено маппингов: {len(values)}")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    load_mapping()