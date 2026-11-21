from psycopg2.extras import Json
from .db import connect

ASSETS = [
    (
        "source.rossmann",
        "source",
        "CSV продаж Rossmann; публичный учебный набор",
        "магазин × дата",
        {
            "path": "data/train.csv",
            "period": "2013-01-01—2015-07-31",
            "fields": {
                "Sales": "дневная выручка, EUR",
                "Customers": "факт покупателей; запрещён как будущий признак",
            },
        },
    ),
    (
        "source.stores",
        "source",
        "Снимок справочника Rossmann",
        "магазин",
        {
            "path": "data/store.csv",
            "limitation": "effective dates неизвестны; исторические атрибуты являются допущением",
        },
    ),
    (
        "source.geography",
        "source",
        "Синтетическая фиксированная география",
        "магазин",
        {"path": "data/store_geo_mapping.csv", "real_locations": False},
    ),
    (
        "source.weather",
        "source",
        "Open-Meteo Historical API, реальные ответы для учебных координат",
        "регион × дата",
        {
            "url": "https://archive-api.open-meteo.com/v1/archive",
            "cache": "data/weather_cache",
            "used_for_forecast": False,
        },
    ),
    (
        "ingestion",
        "process",
        "Порционная загрузка, проверка, карантин, checksum, retry",
        "партия",
        {
            "code": "pas/ingest.py",
            "manifest": "meta.batches",
            "policy": "некорректная строка в карантин; критический сбой блокирует публикацию",
        },
    ),
    (
        "bronze.sales",
        "table",
        "Исходные версии продаж JSONB",
        "hash записи",
        {"key": "row_hash", "lineage": "batch_id → meta.batches → run_id"},
    ),
    (
        "bronze.stores",
        "table",
        "Исходные версии справочника JSONB",
        "hash записи",
        {"key": "row_hash"},
    ),
    (
        "bronze.weather",
        "table",
        "Дневные записи из исходного API-ответа; полный ответ в cache",
        "hash записи",
        {"key": "row_hash", "lineage": "batch_id + cache request/response"},
    ),
    (
        "transform",
        "process",
        "Типизация, дедупликация, UPSERT, SCD2",
        "бизнес-ключ",
        {
            "code": ["pas/ingest.py", "pas/scd.py"],
            "correction": "последняя строка новой версии источника",
        },
    ),
    (
        "silver.sales_daily",
        "table",
        "Типизированные факты продаж",
        "магазин × дата",
        {
            "key": ["store_id", "date"],
            "unit": "EUR",
            "constraints": ["sales>=0", "customers>=0", "FK store_id"],
        },
    ),
    (
        "silver.weather_daily",
        "table",
        "Погода по синтетическим регионам",
        "регион × дата",
        {"temperature_unit": "C", "precipitation_unit": "mm", "geography": "synthetic mapping"},
    ),
    (
        "silver.store_versions",
        "table",
        "Историзация атрибутов SCD2",
        "магазин × [valid_from,valid_to)",
        {"time_policy": "полуоткрытые интервалы; дата действия отличается от recorded_at"},
    ),
    (
        "gold.sales_enriched",
        "view",
        "Продажи + погода + историческая версия магазина",
        "магазин × дата",
        {"join": "s.date>=valid_from AND s.date<valid_to"},
    ),
    (
        "model",
        "process",
        "Seasonal Naive и HistGradientBoosting; 3 временных окна по 42 дня",
        "версия модели",
        {
            "code": "pas/ml.py",
            "lineage": "meta.model_runs: data_hash/code_hash/run_id/artifact_path",
            "excluded": [
                "Customers",
                "future actual weather",
                "synthetic geography",
                "undated store attributes",
            ],
        },
    ),
    (
        "gold.forecasts",
        "table",
        "Неотрицательные прогнозы двух моделей",
        "версия × модель × магазин × дата",
        {"unit": "EUR", "key": ["model_run_id", "model", "store_id", "date"]},
    ),
    (
        "gold.metrics",
        "table",
        "Метрики временной валидации",
        "версия × модель × origin × разрез",
        {
            "MAE": "mean(abs(actual-prediction)), EUR",
            "WAPE": "sum(abs(error))/sum(abs(actual))",
            "RMSPE": "sqrt(mean(((actual-prediction)/actual)^2)) only actual>0",
        },
    ),
    (
        "kpi.forecast_revenue",
        "metric",
        "Ожидаемая выручка выбранных магазинов за горизонт",
        "выбранная модель × период",
        {
            "formula": "sum(gold.latest_forecasts.prediction)",
            "unit": "EUR",
            "owner": "операционный директор",
        },
    ),
    (
        "dashboard.business",
        "dashboard",
        "Какую выручку ожидаем и где высока ошибка?",
        "фильтры пользователя",
        {"code": "dashboard/app.py", "refresh_seconds": 30},
    ),
    (
        "meta.runs",
        "table",
        "Состояния запусков и ошибки, отдельная транзакция журнала",
        "run_id",
        {"freshness": "logical_date для replay; started_at/ended_at для фактического выполнения"},
    ),
    (
        "meta.dq_results",
        "table",
        "Автоматические проверки семи типов",
        "запуск × правило",
        {"blocking": "severity=error; warning допускает публикацию"},
    ),
    (
        "dashboard.operations",
        "dashboard",
        "Можно ли доверять данным и текущему прогнозу?",
        "последние запуски",
        {"code": "dashboard/app.py", "refresh_seconds": 30},
    ),
]
EDGES = [
    ("source.rossmann", "ingestion"),
    ("source.stores", "ingestion"),
    ("source.geography", "ingestion"),
    ("source.weather", "ingestion"),
    ("ingestion", "bronze.sales"),
    ("ingestion", "bronze.stores"),
    ("ingestion", "bronze.weather"),
    ("ingestion", "meta.runs"),
    ("bronze.sales", "transform"),
    ("bronze.stores", "transform"),
    ("bronze.weather", "transform"),
    ("transform", "silver.sales_daily"),
    ("transform", "silver.weather_daily"),
    ("transform", "silver.store_versions"),
    ("transform", "meta.dq_results"),
    ("silver.sales_daily", "gold.sales_enriched"),
    ("silver.weather_daily", "gold.sales_enriched"),
    ("silver.store_versions", "gold.sales_enriched"),
    ("silver.sales_daily", "model"),
    ("model", "gold.forecasts"),
    ("model", "gold.metrics"),
    ("gold.forecasts", "kpi.forecast_revenue"),
    ("kpi.forecast_revenue", "dashboard.business"),
    ("gold.metrics", "dashboard.business"),
    ("gold.sales_enriched", "dashboard.business"),
    ("meta.runs", "dashboard.operations"),
    ("meta.dq_results", "dashboard.operations"),
]


def register_metadata():
    with connect() as conn, conn.cursor() as cur:
        for name, kind, description, grain, definition in ASSETS:
            cur.execute(
                """INSERT INTO meta.assets VALUES(%s,%s,%s,%s,%s,%s,%s)
             ON CONFLICT(name) DO UPDATE SET description=excluded.description,grain=excluded.grain,definition=excluded.definition""",
                (
                    name,
                    kind,
                    description,
                    grain,
                    "Data Engineer" if kind != "metric" else "Операционный директор",
                    "при успешном pipeline",
                    Json(definition),
                ),
            )
        for upstream, downstream in EDGES:
            cur.execute(
                "INSERT INTO meta.lineage VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (upstream, downstream, "см. definition и код актива"),
            )
