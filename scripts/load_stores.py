import pandas as pd
import psycopg2
import json
import os
import numpy as np
from psycopg2.extras import execute_values

DB_PARAMS = {
    "dbname": "rossmann",
    "user": "rossmann",
    "password": "rossmann",
    "host": "localhost",
    "port": "5432"
}

# 🛠 Кастомный энкодер: превращает NaN в null, а numpy-типы в стандартные Python
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            if np.isnan(obj):
                return None  # Превращаем NaN в null
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if pd.isna(obj):
            return None
        return super().default(obj)

def load_stores_to_bronze():
    print("🔄 Подключение к базе данных...")
    conn = psycopg2.connect(**DB_PARAMS)
    cursor = conn.cursor()
    
    try:
        # 1. Запись в журнал
        cursor.execute("""
            INSERT INTO meta.load_log (source_name, status) 
            VALUES ('rossmann_stores_csv', 'running') RETURNING load_id;
        """)
        load_id = cursor.fetchone()[0]
        conn.commit()
        
        file_path = '/Users/macbookpro/pas/pas-course/data/store.csv'
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}")
            
        print(f"📂 Чтение файла: {file_path}")
        
        df = pd.read_csv(file_path)
        
        # Дополнительная страховка: заменяем все NaN на None на уровне DataFrame
        df = df.replace({np.nan: None})
        
        values_to_insert = []
        for _, row in df.iterrows():
            # Используем наш кастомный энкодер
            payload = json.dumps(row.to_dict(), cls=CustomJSONEncoder)
            values_to_insert.append((payload, 'store.csv'))
        
        insert_query = """
            INSERT INTO bronze.raw_stores (payload, source_file)
            VALUES %s
            ON CONFLICT DO NOTHING
        """
        execute_values(cursor, insert_query, values_to_insert)
        conn.commit()
        
        # 2. Обновление журнала
        cursor.execute("""
            UPDATE meta.load_log 
            SET status = 'success', end_time = NOW(), rows_loaded = %s
            WHERE load_id = %s
        """, (len(df), load_id))
        conn.commit()
        print(f"\n🎉 УСПЕХ! Загружено магазинов: {len(df)}")
        
    except Exception as e:
        # Откат транзакции при ошибке
        conn.rollback()
        try:
            cursor.execute("""
                UPDATE meta.load_log 
                SET status = 'failed', end_time = NOW(), error_message = %s
                WHERE load_id = %s
            """, (str(e), load_id))
            conn.commit()
        except Exception:
            pass
        print(f"\n❌ Ошибка загрузки: {e}")
        raise
    finally:
        cursor.close()
        conn.close()
        print("🔌 Соединение с БД закрыто.")

if __name__ == "__main__":
    load_stores_to_bronze()