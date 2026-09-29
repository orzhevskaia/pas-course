import pandas as pd
import random
import os

# Базовые координаты крупных городов Германии (Rossmann — немецкая сеть)
CITIES = [
    ("Berlin", 52.5200, 13.4050),
    ("Frankfurt", 50.1109, 8.6821),
    ("Munich", 48.1351, 11.5820),
    ("Hamburg", 53.5511, 9.9937),
    ("Cologne", 50.9375, 6.9603),
    ("Dusseldorf", 51.2277, 6.7735),
    ("Stuttgart", 48.7758, 9.1829),
    ("Dresden", 51.0504, 13.7373),
    ("Leipzig", 51.3397, 12.3731),
    ("Hannover", 52.3759, 9.7320),
    ("Nuremberg", 49.4521, 11.0767),
    ("Bremen", 53.0793, 8.8017),
    ("Dortmund", 51.5136, 7.4653),
    ("Essen", 51.4556, 7.0116),
    ("Mannheim", 49.4875, 8.4660),
    ("Karlsruhe", 49.0069, 8.4037),
    ("Freiburg", 47.9990, 7.8421),
    ("Rostock", 54.0924, 12.0991),
    ("Kiel", 54.3233, 10.1228),
    ("Augsburg", 48.3705, 10.8978),
]

def generate_geo_mapping():
    # Загружаем справочник магазинов
    stores_path = '/Users/macbookpro/pas/pas-course/data/store.csv'
    stores = pd.read_csv(stores_path)
    
    # Каждому магазину присваиваем случайный город + небольшой случайный сдвиг
    mapping = []
    for _, row in stores.iterrows():
        city_name, base_lat, base_lon = random.choice(CITIES)
        # Сдвиг до ±0.3 градуса (~30 км) для реалистичности
        lat = base_lat + random.uniform(-0.3, 0.3)
        lon = base_lon + random.uniform(-0.3, 0.3)
        
        mapping.append({
            'store_id': int(row['Store']),
            'region_name': city_name,
            'latitude': round(lat, 4),
            'longitude': round(lon, 4)
        })
    
    df = pd.DataFrame(mapping)
    
    # Создаем папку, если её нет
    os.makedirs('/Users/macbookpro/pas/pas-course/data', exist_ok=True)
    output_path = '/Users/macbookpro/pas/pas-course/data/store_geo_mapping.csv'
    df.to_csv(output_path, index=False)
    
    print(f"✅ Создан справочник геолокации: {output_path}")
    print(f"   Магазинов: {len(df)}")
    print(f"   Уникальных регионов: {df['region_name'].nunique()}")
    print(f"\nПример:")
    print(df.head(10).to_string(index=False))

if __name__ == "__main__":
    generate_geo_mapping()