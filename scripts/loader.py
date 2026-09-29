import pandas as pd
import psycopg2
import json
import os
from psycopg2.extras import execute_values # Импортируем для быстрой вставки

# Настройки подключения к PostgreSQL
DB_PARAMS = {
    "dbname": "rossmann",
    "user": "rossmann",
    "password": "rossmann",
    "host": "localhost",
    "port": "5432"
}

def load_to_bronze():
    print("🔄 Подключение к базе данных...")
    conn = psycopg2.connect(**DB_PARAMS)
    cursor = conn.cursor()
    
    # 1. Создаем запись в журнале загрузок
    cursor.execute("""
        INSERT INTO meta.load_log (source_name, status) 
        VALUES ('rossmann_train_csv', 'running') RETURNING load_id;
    """)
    load_id = cursor.fetchone()[0]
    conn.commit()
    
    rows_loaded = 0
    rows_rejected = 0
    
    try:
        file_path = '/Users/macbookpro/pas/pas-course/data/train.csv'
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}. Проверьте путь к train.csv")
            
        print(f"📂 Чтение файла: {file_path}")
        print("⏳ Загрузка началась. Это займет около 10-20 секунд. Пожалуйста, подождите...")
        
        # Увеличиваем размер чанка до 50 000 для максимальной скорости
        chunksize = 50000 
        
        for chunk in pd.read_csv(file_path, chunksize=chunksize):
            # Преобразуем каждую строку чанка в JSON-строку (psycopg2 сам экранирует кавычки)
            values_to_insert = [
                (json.dumps(row.to_dict()), 'train.csv') 
                for _, row in chunk.iterrows()
            ]
            
            # Массовая вставка: один запрос на 50 000 строк вместо 50 000 запросов!
            insert_query = """
                INSERT INTO bronze.raw_sales (payload, source_file)
                VALUES %s
                ON CONFLICT DO NOTHING
            """
            # execute_values автоматически подставит значения вместо %s
            execute_values(cursor, insert_query, values_to_insert)
            
            rows_loaded += len(chunk)
            print(f"  ✅ Обработано {rows_loaded} строк...")
            
        # 2. Обновляем журнал загрузок на "success"
        cursor.execute("""
            UPDATE meta.load_log 
            SET status = 'success', end_time = NOW(), rows_loaded = %s, rows_rejected = %s
            WHERE load_id = %s
        """, (rows_loaded, rows_rejected, load_id))
        conn.commit()
        print(f"\n🎉 УСПЕХ! Всего загружено строк: {rows_loaded}, Отбраковано: {rows_rejected}")
        
    except Exception as e:
        # 3. Обработка сбоев (Требование методички: "Устойчивость к сбоям")
        cursor.execute("""
            UPDATE meta.load_log 
            SET status = 'failed', end_time = NOW(), error_message = %s
            WHERE load_id = %s
        """, (str(e), load_id))
        conn.commit()
        print(f"\n❌ Ошибка загрузки: {e}")
    finally:
        cursor.close()
        conn.close()
        print("🔌 Соединение с БД закрыто.")

if __name__ == "__main__":
    load_to_bronze()